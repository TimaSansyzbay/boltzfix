"""Import an archived run as a completed replay, without executing any action."""

from __future__ import annotations
import json
import zipfile
from .models import RunSummary, SelectionReceipt, digest
from .errors import BoltzFixError
from .store import uid


def import_bundle(store, path):
    with zipfile.ZipFile(path) as z:
        if sum(i.file_size for i in z.infolist()) > 100 * 1024 * 1024:
            raise BoltzFixError(
                "REPLAY_TOO_LARGE", "Replay exceeds 100 MB uncompressed"
            )
        source = RunSummary.model_validate_json(z.read("run.json"))
        if source.selection is None:
            raise BoltzFixError("REPLAY_INVALID", "Archive requires a frozen selection")
        for c in source.candidates:
            if c.run_id != source.id:
                raise BoltzFixError("REPLAY_INVALID", "Candidate run identity differs")
        config = source.config.model_copy(
            update={"name": f"Archived replay / {source.config.name}"[:100]}
        )
        run = store.create_run(config, initial_status="paused")
        store.set_status(
            run,
            "paused",
            capabilities={
                **source.capabilities,
                "archived_replay": True,
                "source_run_id": source.id,
            },
        )
        mapping = {c.id: uid("cand") for c in source.candidates}
        for c in source.candidates:
            artifacts = {}
            for kind, artifact_id in c.artifact_ids.items():
                matches = [
                    n for n in z.namelist() if n.startswith(f"artifacts/{artifact_id}_")
                ]
                if len(matches) != 1:
                    raise BoltzFixError(
                        "REPLAY_INVALID", "Missing/ambiguous archived artifact"
                    )
                content = z.read(matches[0])
                if c.id == source.selection.candidate_id and digest(
                    content
                ) != source.selection.artifact_hashes.get(kind):
                    raise BoltzFixError(
                        "REPLAY_TAMPERED",
                        "Selected artifact hash differs from frozen receipt",
                    )
                filename = matches[0].rsplit("/", 1)[-1]
                artifacts[kind] = store.add_artifact(run, filename, content)
            store.add_candidate(
                c.model_copy(
                    update={
                        "id": mapping[c.id],
                        "run_id": run,
                        "parent_id": mapping.get(c.parent_id),
                        "artifact_ids": artifacts,
                        "provenance": {
                            **c.provenance,
                            "archived_replay": True,
                            "source_candidate_id": c.id,
                        },
                    }
                )
            )
        events = [
            json.loads(line)
            for line in z.read("events.jsonl").decode().splitlines()
            if line
        ]
        for event in events:
            if event["type"] in {
                "proposal",
                "review",
                "action_executed",
                "refinement_unavailable",
                "disagreement",
            }:
                data = json.dumps(event["data"])
                for before, after in mapping.items():
                    data = data.replace(before, after)
                store.event(
                    run,
                    event["type"],
                    {
                        **json.loads(data),
                        "archived": True,
                        "source_time": event["time"],
                    },
                )
        if "actions.json" in z.namelist():
            for action in json.loads(z.read("actions.json")):
                with store.connection(immediate=True) as connection:
                    connection.execute(
                        "INSERT INTO actions(id,run_id,action,status,reservation,payload,started,finished,actual) VALUES(?,?,?,?,?,?,?,?,?)",
                        (
                            uid("act"),
                            run,
                            action["action"],
                            (
                                "unknown"
                                if action["status"] == "reserved"
                                else action["status"]
                            ),
                            json.dumps(action["reservation"]),
                            json.dumps({**action["payload"], "archived": True}),
                            action["started_at"],
                            action["finished_at"],
                            json.dumps(action["actual"]),
                        ),
                    )
        selected = (
            store.candidate(mapping[source.selection.candidate_id])
            if source.selection.candidate_id
            else None
        )
        store.freeze(
            run,
            SelectionReceipt(
                candidate_id=selected.id if selected else None,
                artifact_hashes=(
                    {
                        k: store.artifact(v)[0]["sha256"]
                        for k, v in selected.artifact_ids.items()
                    }
                    if selected
                    else {}
                ),
                config_hash=store.row(run)["config_hash"],
                physical_valid=source.selection.physical_valid,
                limitations=[
                    "ARCHIVED REPLAY: no new inference, decisions or mechanics were executed.",
                    *source.selection.limitations,
                ],
            ),
        )
        store.set_status(run, "completed")
        store.event(
            run,
            "archive_imported",
            {
                "source_run_id": source.id,
                "historical_usage": source.budget.model_dump(),
                "live_work_dispatched": False,
            },
        )
        store.export_events(run)
        return run
