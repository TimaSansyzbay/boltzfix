from __future__ import annotations
import asyncio
from contextlib import asynccontextmanager
import io
import json
import os
from pathlib import Path
import tempfile
import zipfile
from fastapi import FastAPI, HTTPException, Request, UploadFile, File, Form
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse, Response
from .chemistry import input_molecule
from .engine import worker_loop
from .errors import BoltzFixError
from .models import RunConfig, RunSummary, Candidate, DiagnosticReport, TERMINAL
from .providers import MockProvider, NvidiaProvider, NebiusProvider
from .providers.diffdock import DiffDockProvider
from .refinement import availability
from .store import Store
from .validation import diagnostics
from .structure_io import protein_input, prepare_pair


def bundle(store, run_id):
    summary = store.summary(run_id)
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("run.json", summary.model_dump_json(indent=2))
        archive.writestr(
            "events.jsonl", "".join(json.dumps(e) + "\n" for e in store.events(run_id))
        )
        archive.writestr(
            "actions.json",
            json.dumps([a.model_dump() for a in store.actions(run_id)], indent=2),
        )
        archive.writestr(
            "README.txt",
            "BoltzFix run bundle. Synthetic runs are NOT scientific results. No experimental reference is included. Artifact hashes are in run.json. Timing is observed service latency, not GPU time.\n",
        )
        with store.connection() as c:
            rows = c.execute(
                "SELECT id FROM artifacts WHERE run_id=?", (run_id,)
            ).fetchall()
        for r in rows:
            meta, path = store.artifact(r[0])
            archive.write(path, f"artifacts/{meta['id']}_{meta['filename']}")
    return buffer.getvalue()


def create_app(root=None, run_worker=True):
    store = Store(root or os.getenv("BOLTZFIX_DATA", ".boltzfix"))

    @asynccontextmanager
    async def lifespan(app):
        stop = asyncio.Event()
        task = asyncio.create_task(worker_loop(store, stop)) if run_worker else None
        yield
        stop.set()
        if task:
            try:
                await asyncio.wait_for(task, 5)
            except asyncio.TimeoutError:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)

    app = FastAPI(title="BoltzFix scientific API", version="0.1.0", lifespan=lifespan)
    app.state.store = store
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://127.0.0.1:5173", "http://localhost:5173"],
        allow_methods=["GET", "POST"],
        allow_headers=["Content-Type", "Last-Event-ID"],
    )

    @app.exception_handler(BoltzFixError)
    async def scientific_error(request, exc):
        return JSONResponse(
            {"error": exc.public()},
            status_code=(
                404
                if exc.code == "NOT_FOUND"
                else 409 if exc.code in {"SELECTION_FROZEN", "CANCELLED"} else 422
            ),
        )

    @app.middleware("http")
    async def private_write_origin(request, call_next):
        # Loopback deployment with no login: reject cross-site form/JSON writes.
        if request.method == "POST":
            origin = request.headers.get("origin")
            if origin and origin not in {
                "http://127.0.0.1:5173",
                "http://localhost:5173",
                "http://127.0.0.1:8000",
                "http://localhost:8000",
            }:
                return JSONResponse(
                    {
                        "error": {
                            "code": "ORIGIN_DENIED",
                            "message": "Local application origin required",
                        }
                    },
                    status_code=403,
                )
        return await call_next(request)

    @app.get("/v1/health")
    async def health():
        return {
            "status": "ok",
            "worker_enabled": run_worker,
            "schema_version": 1,
            "physics": availability(),
        }

    @app.get("/v1/capabilities")
    async def capabilities():
        providers = await asyncio.gather(
            *[
                p.capabilities()
                for p in [
                    MockProvider(),
                    NebiusProvider(),
                    NvidiaProvider(),
                    DiffDockProvider(),
                ]
            ]
        )
        return {
            "providers": [p.model_dump() for p in providers],
            "physics": availability(),
            "actions": ["resample", "minimize", "stop"],
            "live_default": False,
            "reference_evaluation": "offline_only",
        }

    @app.get("/v1/runs")
    async def runs():
        return store.list_runs()

    @app.post("/v1/runs", status_code=202)
    async def create_run(config: RunConfig):
        input_molecule(config.ligand_smiles)
        run_id = store.create_run(config)
        return {
            "id": run_id,
            "status": "queued",
            "synthetic": config.provider == "mock",
        }

    @app.get("/v1/runs/{run_id}", response_model=RunSummary)
    async def run(run_id: str):
        return store.summary(run_id)

    @app.post("/v1/runs/import", status_code=202)
    async def import_run(
        protein: UploadFile = File(...),
        ligands: list[UploadFile] = File(...),
        smiles: str = Form(...),
        settings: str = Form("{}"),
    ):
        try:
            values = json.loads(settings)
            if not isinstance(values, dict):
                raise ValueError("Settings must be an object")
            raw = await protein.read(3000001)
            if len(raw) > 3000000:
                raise HTTPException(413, "Maximum 3 MB protein input")
            text = raw.decode("utf-8")
            _, sequence = protein_input(text)
            poses = []
            for upload in ligands:
                data = await upload.read(4000001)
                if len(data) > 4000000:
                    raise HTTPException(413, "Maximum 4 MB per SDF file")
                for index, part in enumerate(data.decode("utf-8").split("$$$$")):
                    if not part.strip():
                        continue
                    if index:
                        part = (
                            part.removeprefix("\r\n")
                            if part.startswith("\r\n")
                            else part.removeprefix("\n")
                        )
                    sdf = part.rstrip() + "\n$$$$\n"
                    prepare_pair(text, sdf, smiles)
                    poses.append(sdf)
            if not 1 <= len(poses) <= 4:
                raise ValueError("Supply 1–4 poses for the same ligand")
            config = RunConfig.model_validate(
                {
                    **values,
                    "sequence": sequence,
                    "ligand_smiles": smiles,
                    "protein_pdb": text,
                    "input_ligands": poses,
                    "provider": "imported",
                    "initial_samples": 0,
                    "protocol_version": "boltzfix-v2",
                }
            )
        except (ValueError, UnicodeDecodeError) as exc:
            raise HTTPException(422, str(exc)[:1200]) from exc
        run_id = store.create_run(config)
        return {"id": run_id, "status": "queued", "synthetic": False}

    @app.get("/v1/examples/{name}")
    async def example(name: str):
        if name not in {"retinol", "brd4"}:
            raise HTTPException(404, "Unknown example")
        path = (
            Path(__file__).resolve().parents[1]
            / "fixtures"
            / "examples"
            / (name + ".json")
        )
        if not path.is_file():
            raise HTTPException(404, "Example not installed")
        return json.loads(path.read_text())

    @app.post("/v1/structures/protein")
    async def inspect_protein(protein: UploadFile = File(...)):
        raw = await protein.read(3000001)
        if len(raw) > 3000000:
            raise HTTPException(413, "Maximum 3 MB protein input")
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise HTTPException(422, "Upload a text PDB file") from exc
        pdb, sequence = protein_input(text)
        return {"protein_pdb": pdb, "sequence": sequence, "residues": len(sequence)}

    @app.get("/v1/runs/{run_id}/report")
    async def research_report(run_id: str, format: str = "html"):
        from .reporting import render_report

        if format not in {"html", "md"}:
            raise HTTPException(422, "Choose html or md")
        body = render_report(store, run_id, format)
        return Response(
            body,
            media_type="text/html" if format == "html" else "text/markdown",
            headers={
                "Content-Disposition": f'attachment; filename="{run_id}.report.{format}"',
                "X-Content-Type-Options": "nosniff",
            },
        )

    @app.get("/v1/runs/{run_id}/events")
    async def events(run_id: str, request: Request, after: int = 0):
        store.row(run_id)
        try:
            cursor = max(0, after, int(request.headers.get("last-event-id", "0")))
        except ValueError:
            raise HTTPException(422, "Invalid event cursor")

        async def stream():
            nonlocal cursor
            while not await request.is_disconnected():
                for event in store.events(run_id, cursor):
                    cursor = event["seq"]
                    yield f"id: {cursor}\ndata: {json.dumps(event)}\n\n"
                if store.row(run_id)["status"] in TERMINAL:
                    yield "event: end\ndata: {}\n\n"
                    return
                yield ": heartbeat\n\n"
                await asyncio.sleep(0.4)

        return StreamingResponse(
            stream(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    @app.post("/v1/runs/{run_id}/cancel", status_code=202)
    async def cancel(run_id: str):
        store.cancel(run_id)
        return {
            "id": run_id,
            "cancel_requested": True,
            "remote_cancellation": "not guaranteed; reservation retained",
        }

    @app.get("/v1/runs/{run_id}/export")
    async def export(run_id: str):
        return Response(
            await asyncio.to_thread(bundle, store, run_id),
            media_type="application/zip",
            headers={"Content-Disposition": f'attachment; filename="{run_id}.zip"'},
        )

    @app.get("/v1/candidates/{candidate_id}", response_model=Candidate)
    async def candidate(candidate_id: str):
        return store.candidate(candidate_id)

    @app.get("/v1/artifacts/{artifact_id}")
    async def artifact(artifact_id: str):
        meta, path = store.artifact(artifact_id)
        return FileResponse(
            path,
            media_type=meta["media_type"],
            filename=meta["filename"],
            headers={"ETag": meta["sha256"], "X-Content-Type-Options": "nosniff"},
        )

    @app.post("/v1/diagnostics", response_model=DiagnosticReport)
    async def validate(
        protein: UploadFile = File(...),
        ligand: UploadFile = File(...),
        smiles: str = Form(...),
    ):
        input_molecule(smiles)
        with tempfile.TemporaryDirectory(prefix="boltzfix-diag-") as folder:
            paths = []
            for upload, name in [(protein, "protein.pdb"), (ligand, "ligand.sdf")]:
                data = await upload.read(5 * 1024 * 1024 + 1)
                if len(data) > 5 * 1024 * 1024:
                    raise HTTPException(413, "Maximum 5 MB per structure")
                p = Path(folder) / name
                p.write_bytes(data)
                paths.append(p)
            return await asyncio.to_thread(diagnostics, *paths, smiles)

    return app


app = create_app()
