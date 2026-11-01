from __future__ import annotations
import asyncio
import json
import os
from pathlib import Path
import typer
from .models import RunConfig, BudgetLimits
from .store import Store

app = typer.Typer(
    help="BoltzFix: bounded investigations and isolated offline evaluation"
)


def store_at(data: Path):
    return Store(data)


@app.command()
def serve(host: str = "127.0.0.1", port: int = 8000):
    """Start the local API and scientific worker."""
    if host not in {"127.0.0.1", "localhost", "::1"}:
        raise typer.BadParameter("v1 is private: bind a loopback interface")
    import uvicorn

    uvicorn.run("boltzfix.api:app", host=host, port=port)


@app.command()
def run(config: Path, data: Path = Path(".boltzfix")):
    """Execute an immutable JSON run configuration; never reads keys from this file."""
    from .engine import Engine

    s = store_at(data)
    r = s.create_run(RunConfig.model_validate_json(config.read_text()))
    s.claim(r, "cli")
    asyncio.run(Engine(s).run(r))
    typer.echo(s.summary(r).model_dump_json(indent=2))


@app.command()
def demo(data: Path = Path(".boltzfix"), policy: str = "collective"):
    """Artificial scaffold and authored mock agents; no scientific inference."""
    from .engine import Engine

    s = store_at(data)
    c = RunConfig(
        name="Synthetic investigation",
        sequence="A" * 100,
        ligand_smiles="CC(=O)Oc1ccccc1C(=O)O",
        policy=policy,
    )
    r = s.create_run(c)
    s.claim(r, "cli")
    asyncio.run(Engine(s).run(r))
    typer.echo(
        json.dumps(
            {
                "run_id": r,
                "status": s.row(r)["status"],
                "synthetic": True,
                "warning": "Not scientific results",
            },
            indent=2,
        )
    )


@app.command()
def preflight(data: Path = Path(".boltzfix"), discover_nebius: bool = False):
    """Sanitized capabilities. Discovery only runs with explicit live configuration."""
    from .providers import MockProvider, NvidiaProvider, NebiusProvider
    from .refinement import availability

    async def go():
        result = {
            "providers": [
                (await p.capabilities()).model_dump()
                for p in [MockProvider(), NebiusProvider(), NvidiaProvider()]
            ],
            "physics": availability(),
        }
        if discover_nebius:
            tools = await NebiusProvider().discover()
            folder = data / "preflight"
            folder.mkdir(parents=True, exist_ok=True)
            (folder / "nebius-tools.json").write_text(json.dumps(tools, indent=2))
            result["discovered_tools"] = len(tools)
        return result

    typer.echo(json.dumps(asyncio.run(go()), indent=2))


@app.command("diagnostics")
def diagnose(protein: Path, ligand: Path, smiles: str, output: Path):
    """Reusable prediction-only validation, independent of agents and references."""
    from .validation import diagnostics

    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(diagnostics(protein, ligand, smiles).model_dump_json(indent=2))
    typer.echo(str(output))


@app.command()
def evaluate(
    run_id: str,
    reference_protein: Path,
    reference_ligand: Path,
    output: Path,
    data: Path = Path(".boltzfix"),
):
    """Offline reference evaluation after selection freeze. Never an agent tool."""
    from .evaluator import evaluate_run

    typer.echo(
        json.dumps(
            evaluate_run(
                store_at(data), run_id, reference_protein, reference_ligand, output
            ),
            indent=2,
        )
    )


@app.command()
def curate(inventory: Path, output: Path, dev_count: int = 6, eval_count: int = 20):
    from .dataset import curate as build

    typer.echo(json.dumps(build(inventory, output, dev_count, eval_count), indent=2))


@app.command("export")
def export_run(run_id: str, output: Path, data: Path = Path(".boltzfix")):
    from .api import bundle

    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(bundle(store_at(data), run_id))
    typer.echo(str(output))


@app.command()
def register(
    manifest: Path,
    output: Path,
    provider: str = "mock",
    hybrid_schedule: str = "sample_then_minimize",
    partition: str = "evaluation",
    model: str = "claude-opus-5-5",
):
    from .benchmark import register as freeze

    typer.echo(
        json.dumps(
            freeze(manifest, output, provider, hybrid_schedule, model, partition),
            indent=2,
        )
    )


@app.command()
def benchmark(registration: Path, output: Path, data: Path = Path(".boltzfix")):
    from .benchmark import campaign

    typer.echo(
        json.dumps(
            asyncio.run(campaign(store_at(data), registration, output)), indent=2
        )
    )


@app.command()
def analyze(evaluations: Path, output: Path):
    from .benchmark import analyze as report

    typer.echo(json.dumps(report(evaluations, output), indent=2))


@app.command("evaluate-campaign")
def evaluate_campaign(
    registration: Path,
    journal: Path,
    references: Path,
    output: Path,
    data: Path = Path(".boltzfix"),
):
    from .benchmark import evaluate_campaign as score

    typer.echo(
        json.dumps(
            score(store_at(data), registration, journal, references, output), indent=2
        )
    )


@app.command()
def replay(archive: Path, data: Path = Path(".boltzfix")):
    from .replay import import_bundle

    typer.echo(
        json.dumps(
            {
                "run_id": import_bundle(store_at(data), archive),
                "archived_replay": True,
                "live_work_dispatched": False,
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    app()
