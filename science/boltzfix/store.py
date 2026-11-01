from __future__ import annotations
import json
import math
import os
from pathlib import Path
import sqlite3
import time
import uuid
from contextlib import contextmanager
from .errors import BoltzFixError
from .models import (
    RunConfig,
    RunSummary,
    BudgetLedger,
    BudgetLimits,
    Candidate,
    SelectionReceipt,
    ActionRecord,
    utcnow,
    digest,
)


def uid(prefix: str):
    return f"{prefix}_{uuid.uuid4().hex[:16]}"


class Store:
    def __init__(self, root: Path | str):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.db = self.root / "runs.sqlite3"
        with self.connection() as c:
            c.executescript(
                """
            PRAGMA journal_mode=WAL;
            CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT NOT NULL);
            INSERT OR IGNORE INTO meta VALUES('schema_version','1');
            CREATE TABLE IF NOT EXISTS runs(
              id TEXT PRIMARY KEY, status TEXT NOT NULL, config TEXT NOT NULL, config_hash TEXT NOT NULL,
              created TEXT NOT NULL, updated TEXT NOT NULL, started REAL, cancel INTEGER DEFAULT 0,
              capabilities TEXT NOT NULL DEFAULT '{}', selection TEXT, error TEXT, owner TEXT, heartbeat REAL);
            CREATE TABLE IF NOT EXISTS candidates(id TEXT PRIMARY KEY, run_id TEXT NOT NULL, data TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS artifacts(id TEXT PRIMARY KEY, run_id TEXT NOT NULL, path TEXT NOT NULL,
              sha256 TEXT NOT NULL, media_type TEXT NOT NULL, filename TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS events(seq INTEGER PRIMARY KEY AUTOINCREMENT,run_id TEXT NOT NULL,
              type TEXT NOT NULL,time TEXT NOT NULL,data TEXT NOT NULL);
            CREATE INDEX IF NOT EXISTS events_run ON events(run_id,seq);
            CREATE TABLE IF NOT EXISTS actions(id TEXT PRIMARY KEY,run_id TEXT NOT NULL,action TEXT NOT NULL,
              status TEXT NOT NULL,reservation TEXT NOT NULL,payload TEXT NOT NULL,started TEXT NOT NULL,
              finished TEXT,actual TEXT NOT NULL DEFAULT '{}',remote_id TEXT);
            CREATE INDEX IF NOT EXISTS actions_run ON actions(run_id);
            CREATE TABLE IF NOT EXISTS campaign_usage(id INTEGER PRIMARY KEY CHECK(id=1),samples INTEGER NOT NULL,
              llm_calls INTEGER NOT NULL,llm_tokens INTEGER NOT NULL,usd REAL NOT NULL);
            INSERT OR IGNORE INTO campaign_usage VALUES(1,0,0,0,0);
            CREATE TABLE IF NOT EXISTS locks(name TEXT PRIMARY KEY,owner TEXT NOT NULL,expires REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS agent_dispatches(action_id TEXT PRIMARY KEY,dispatched_at TEXT NOT NULL);
            """
            )

    @contextmanager
    def connection(self, immediate=False):
        c = sqlite3.connect(self.db, timeout=30)
        c.row_factory = sqlite3.Row
        try:
            c.execute("PRAGMA foreign_keys=ON")
            if immediate:
                c.execute("BEGIN IMMEDIATE")
            yield c
            c.commit()
        except Exception:
            c.rollback()
            raise
        finally:
            c.close()

    def event(self, run_id: str, kind: str, data: dict, c=None):
        def insert(conn):
            cur = conn.execute(
                "INSERT INTO events(run_id,type,time,data) VALUES(?,?,?,?)",
                (run_id, kind, utcnow(), json.dumps(data, allow_nan=False)),
            )
            return cur.lastrowid

        if c is not None:
            return insert(c)
        with self.connection(immediate=True) as conn:
            seq = insert(conn)
        return seq

    def events(self, run_id: str, after=0):
        with self.connection() as c:
            rows = c.execute(
                "SELECT * FROM events WHERE run_id=? AND seq>? ORDER BY seq",
                (run_id, after),
            ).fetchall()
        return [
            {
                "seq": r["seq"],
                "run_id": r["run_id"],
                "type": r["type"],
                "time": r["time"],
                "data": json.loads(r["data"]),
            }
            for r in rows
        ]

    def create_run(self, config: RunConfig, initial_status="queued"):
        if initial_status not in {"queued", "paused"}:
            raise ValueError("Invalid initial status")
        run_id, now = uid("run"), utcnow()
        with self.connection(immediate=True) as c:
            c.execute(
                "INSERT INTO runs(id,status,config,config_hash,created,updated) VALUES(?,?,?,?,?,?)",
                (
                    run_id,
                    initial_status,
                    config.model_dump_json(),
                    digest(config.model_dump()),
                    now,
                    now,
                ),
            )
            self.event(
                run_id,
                "run_created",
                {"synthetic": config.provider == "mock", "policy": config.policy},
                c,
            )
        return run_id

    def row(self, run_id):
        with self.connection() as c:
            row = c.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()
        if row is None:
            raise BoltzFixError("NOT_FOUND", "Run not found")
        return dict(row)

    def list_runs(self):
        with self.connection() as c:
            rows = c.execute(
                "SELECT id,status,config,created,updated FROM runs ORDER BY created DESC LIMIT 100"
            ).fetchall()
        return [
            {
                "id": r["id"],
                "status": r["status"],
                "name": json.loads(r["config"])["name"],
                "synthetic": json.loads(r["config"])["provider"] == "mock",
                "created_at": r["created"],
                "updated_at": r["updated"],
            }
            for r in rows
        ]

    def set_status(self, run_id, status, *, error=None, capabilities=None):
        with self.connection(immediate=True) as c:
            c.execute(
                "UPDATE runs SET status=?,updated=?,error=? WHERE id=?",
                (status, utcnow(), json.dumps(error) if error else None, run_id),
            )
            if capabilities is not None:
                c.execute(
                    "UPDATE runs SET capabilities=? WHERE id=?",
                    (json.dumps(capabilities), run_id),
                )
            self.event(run_id, "status", {"status": status, "error": error}, c)

    def claim(self, run_id, owner):
        with self.connection(immediate=True) as c:
            row = c.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()
            if not row or row["status"] != "queued" or row["cancel"]:
                return False
            c.execute(
                "UPDATE runs SET status='preparing',started=COALESCE(started,?),owner=?,heartbeat=?,updated=? WHERE id=?",
                (time.time(), owner, time.time(), utcnow(), run_id),
            )
            return True

    def queued(self):
        with self.connection() as c:
            return [
                r[0]
                for r in c.execute(
                    "SELECT id FROM runs WHERE status='queued' AND cancel=0 ORDER BY created"
                )
            ]

    def acquire_worker(self, owner, ttl=30):
        with self.connection(immediate=True) as c:
            row = c.execute(
                "SELECT * FROM locks WHERE name='scientific_worker'"
            ).fetchone()
            if row and row["owner"] != owner and row["expires"] > time.time():
                return False
            c.execute(
                "INSERT OR REPLACE INTO locks VALUES('scientific_worker',?,?)",
                (owner, time.time() + ttl),
            )
            return True

    def heartbeat(self, owner, run_id=None):
        with self.connection(immediate=True) as c:
            c.execute(
                "UPDATE locks SET expires=? WHERE name='scientific_worker' AND owner=?",
                (time.time() + 30, owner),
            )
            if run_id:
                c.execute(
                    "UPDATE runs SET heartbeat=? WHERE id=? AND owner=?",
                    (time.time(), run_id, owner),
                )

    def recover_stale(self):
        with self.connection(immediate=True) as c:
            rows = c.execute(
                "SELECT id FROM runs WHERE status IN ('preparing','running') AND heartbeat<?",
                (time.time() - 45,),
            ).fetchall()
            for r in rows:
                c.execute(
                    "UPDATE actions SET status='unknown',finished=? WHERE run_id=? AND status='reserved'",
                    (utcnow(), r[0]),
                )
                c.execute(
                    "UPDATE runs SET status='paused',updated=?,error=? WHERE id=?",
                    (
                        utcnow(),
                        json.dumps(
                            {
                                "code": "RECOVERY_REQUIRED",
                                "message": "Worker stopped. Outstanding actions retained; no automatic resubmission.",
                            }
                        ),
                        r[0],
                    ),
                )
                self.event(
                    r[0],
                    "recovery_paused",
                    {"reason": "Outstanding work requires reconciliation"},
                    c,
                )

    def cancel(self, run_id):
        self.row(run_id)
        with self.connection(immediate=True) as c:
            c.execute(
                "UPDATE runs SET cancel=1,updated=? WHERE id=?", (utcnow(), run_id)
            )
            c.execute(
                "UPDATE runs SET status='cancelled' WHERE id=? AND status IN ('queued','paused')",
                (run_id,),
            )
            self.event(run_id, "cancel_requested", {}, c)

    def check_active(self, run_id):
        row = self.row(run_id)
        if row["selection"]:
            raise BoltzFixError(
                "SELECTION_FROZEN", "This investigation is already frozen"
            )
        if row["cancel"]:
            raise BoltzFixError("CANCELLED", "Cancellation requested")
        config = RunConfig.model_validate_json(row["config"])
        if (
            row["started"]
            and time.time() - row["started"] >= config.budget.wall_seconds
        ):
            raise BoltzFixError("BUDGET_EXHAUSTED", "Elapsed-time allowance exhausted")
        return config

    @staticmethod
    def _totals(rows):
        used, reserved = {}, {}
        for row in rows:
            target = reserved if row["status"] == "reserved" else used
            amounts = json.loads(row["reservation"])
            actual = json.loads(row["actual"])
            # Unknown/failed work is conservatively charged; completed LLM usage settles to actual.
            if (row["status"] == "completed" and "llm_tokens" in actual) or (
                row["status"] == "failed"
                and actual.get("provider_dispatched") is False
                and actual.get("llm_tokens") == 0
            ):
                amounts["llm_tokens"] = actual["llm_tokens"]
            for k, v in amounts.items():
                target[k] = target.get(k, 0) + v
        return used, reserved

    def reserve(self, run_id, action, amounts: dict[str, int], payload=None):
        if any(not isinstance(v, int) or v < 0 for v in amounts.values()):
            raise BoltzFixError("INPUT_INVALID", "Invalid reservation")
        action_id = uid("act")
        with self.connection(immediate=True) as c:
            row = c.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()
            if not row:
                raise BoltzFixError("NOT_FOUND", "Run not found")
            if row["selection"] or row["cancel"]:
                raise BoltzFixError("CANCELLED", "Investigation is cancelled or frozen")
            config = RunConfig.model_validate_json(row["config"])
            if (
                row["started"]
                and time.time() - row["started"] >= config.budget.wall_seconds
            ):
                raise BoltzFixError(
                    "BUDGET_EXHAUSTED", "Elapsed-time allowance exhausted"
                )
            rows = c.execute(
                "SELECT * FROM actions WHERE run_id=?", (run_id,)
            ).fetchall()
            used, reserved = self._totals(rows)
            if any(r["status"] == "unknown" for r in rows):
                raise BoltzFixError(
                    "OUTSTANDING_REMOTE_JOB",
                    "Unresolved work prevents further dispatch",
                    pause=True,
                )
            limits = config.budget.model_dump()
            for key, value in amounts.items():
                if (
                    key not in limits
                    or used.get(key, 0) + reserved.get(key, 0) + value > limits[key]
                ):
                    raise BoltzFixError(
                        "BUDGET_EXHAUSTED", f"{key} allowance exhausted"
                    )
            if (
                action in {"predict", "minimize"}
                and c.execute(
                    "SELECT 1 FROM actions WHERE status='reserved' AND action IN ('predict','minimize') LIMIT 1"
                ).fetchone()
            ):
                raise BoltzFixError(
                    "ACTION_IN_FLIGHT", "One scientific action is already running"
                )
            if action == "minimize":
                parent = (payload or {}).get("candidate_id")
                if any(
                    r["action"] == "minimize"
                    and json.loads(r["payload"]).get("candidate_id") == parent
                    for r in rows
                ):
                    raise BoltzFixError(
                        "ALREADY_REFINED",
                        "This parent already has a refinement attempt",
                    )
            if config.provider != "mock":
                campaign = c.execute(
                    "SELECT * FROM campaign_usage WHERE id=1"
                ).fetchone()
                cost_bound = float((payload or {}).get("cost_bound_usd", 0))
                verified_free_prediction = (
                    action == "predict"
                    and (
                        config.provider in {"nvidia_nim", "auto"}
                        and (payload or {}).get("provider") == "nvidia_nim"
                        and os.getenv("BOLTZFIX_NVIDIA_FREE_TRIAL_CONFIRMED") == "1"
                        or config.provider == "nvidia_diffdock"
                        and (payload or {}).get("provider") == "nvidia_diffdock"
                        and os.getenv("BOLTZFIX_DIFFDOCK_FREE_TRIAL_CONFIRMED") == "1"
                    )
                    and (payload or {}).get("billing_mode") == "free_development_api"
                    and cost_bound == 0
                )
                if (
                    not math.isfinite(cost_bound)
                    or action in {"predict", "llm"}
                    and (
                        cost_bound < 0
                        or cost_bound == 0
                        and not verified_free_prediction
                    )
                ):
                    raise BoltzFixError(
                        "COST_BOUND_REQUIRED",
                        "Configure a verified sponsor cost upper bound before live dispatch",
                        pause=True,
                    )
                if (
                    campaign["samples"] + amounts.get("samples", 0) > 500
                    or campaign["llm_calls"] + amounts.get("llm_calls", 0) > 1500
                ):
                    raise BoltzFixError(
                        "CAMPAIGN_EXHAUSTED", "Campaign allowance exhausted", pause=True
                    )
                if campaign["usd"] + cost_bound > min(
                    50.0, float(os.getenv("BOLTZFIX_SPONSOR_USD_CAP", "50"))
                ):
                    raise BoltzFixError(
                        "CAMPAIGN_EXHAUSTED",
                        "Sponsor cost allowance exhausted",
                        pause=True,
                    )
                c.execute(
                    "UPDATE campaign_usage SET samples=samples+?,llm_calls=llm_calls+?,usd=usd+? WHERE id=1",
                    (
                        amounts.get("samples", 0),
                        amounts.get("llm_calls", 0),
                        cost_bound,
                    ),
                )
            c.execute(
                "INSERT INTO actions(id,run_id,action,status,reservation,payload,started) VALUES(?,?,?,'reserved',?,?,?)",
                (
                    action_id,
                    run_id,
                    action,
                    json.dumps(amounts),
                    json.dumps(payload or {}),
                    utcnow(),
                ),
            )
            self.event(
                run_id,
                "action_reserved",
                {
                    "action_id": action_id,
                    "action": action,
                    "amounts": amounts,
                    "payload": payload or {},
                },
                c,
            )
        return action_id

    def settle(self, action_id, status="completed", actual=None, remote_id=None):
        actual = actual or {}
        with self.connection(immediate=True) as c:
            row = c.execute("SELECT * FROM actions WHERE id=?", (action_id,)).fetchone()
            if not row or row["status"] != "reserved":
                raise BoltzFixError("INVALID_TRANSITION", "Action is not reserved")
            reservation = json.loads(row["reservation"])
            config = json.loads(
                c.execute(
                    "SELECT config FROM runs WHERE id=?", (row["run_id"],)
                ).fetchone()[0]
            )
            if (
                row["action"] == "llm"
                and config["provider"] != "mock"
                and status == "failed"
                and actual.get("provider_dispatched") is False
                and actual.get("llm_tokens") == 0
            ):
                # Confirmed local/preflight failures generate no paid tokens.
                # Retain the attempted call; release only its unused cost bound.
                released = float(json.loads(row["payload"]).get("cost_bound_usd", 0))
                c.execute(
                    "UPDATE campaign_usage SET usd=MAX(0,usd-?) WHERE id=1", (released,)
                )
                actual["cost_bound_released_usd"] = released
            if actual.get("llm_tokens", 0) > reservation.get("llm_tokens", 0):
                status = "unknown"
                actual["budget_violation"] = True
            c.execute(
                "UPDATE actions SET status=?,actual=?,finished=?,remote_id=? WHERE id=?",
                (status, json.dumps(actual), utcnow(), remote_id, action_id),
            )
            if config["provider"] != "mock":
                c.execute(
                    "UPDATE campaign_usage SET llm_tokens=llm_tokens+? WHERE id=1",
                    (actual.get("llm_tokens", 0),),
                )
            self.event(
                row["run_id"],
                "action_settled",
                {
                    "action_id": action_id,
                    "action": row["action"],
                    "status": status,
                    "actual": actual,
                },
                c,
            )

    def actions(self, run_id):
        with self.connection() as c:
            rows = c.execute(
                "SELECT * FROM actions WHERE run_id=? ORDER BY started,id", (run_id,)
            ).fetchall()
        return [
            ActionRecord(
                id=r["id"],
                run_id=run_id,
                action=r["action"],
                status=r["status"],
                reservation=json.loads(r["reservation"]),
                payload=json.loads(r["payload"]),
                started_at=r["started"],
                finished_at=r["finished"],
                actual=json.loads(r["actual"]),
            )
            for r in rows
        ]

    def ledger(self, run_id):
        row = self.row(run_id)
        with self.connection() as c:
            rows = c.execute(
                "SELECT * FROM actions WHERE run_id=?", (run_id,)
            ).fetchall()
        used, reserved = self._totals(rows)
        return BudgetLedger(
            limits=RunConfig.model_validate_json(row["config"]).budget,
            used=used,
            reserved=reserved,
            wall_seconds=(
                max(0, time.time() - row["started"])
                if row["started"]
                and row["status"]
                not in {"completed", "partial", "failed", "cancelled", "paused"}
                else self._finished_wall(row)
            ),
            outstanding_remote=[r["id"] for r in rows if r["status"] == "unknown"],
        )

    @staticmethod
    def _finished_wall(row):
        from datetime import datetime

        return (
            max(0, datetime.fromisoformat(row["updated"]).timestamp() - row["started"])
            if row["started"]
            else 0
        )

    def add_artifact(
        self,
        run_id,
        filename,
        content: bytes | str,
        media_type="application/octet-stream",
    ):
        if Path(filename).name != filename:
            raise BoltzFixError("INPUT_INVALID", "Artifact names must be basenames")
        artifact_id = uid("art")
        folder = self.root / "runs" / run_id
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / f"{artifact_id}_{filename}"
        data = content.encode() if isinstance(content, str) else content
        if len(data) > 100 * 1024 * 1024:
            raise BoltzFixError("ARTIFACT_TOO_LARGE", "Artifact exceeds 100 MB")
        temp = path.with_suffix(path.suffix + ".tmp")
        temp.write_bytes(data)
        temp.replace(path)
        with self.connection(immediate=True) as c:
            if c.execute("SELECT selection FROM runs WHERE id=?", (run_id,)).fetchone()[
                0
            ]:
                path.unlink()
                raise BoltzFixError(
                    "SELECTION_FROZEN", "Frozen run artifacts are immutable"
                )
            c.execute(
                "INSERT INTO artifacts VALUES(?,?,?,?,?,?)",
                (
                    artifact_id,
                    run_id,
                    str(path.relative_to(self.root)),
                    digest(data),
                    media_type,
                    filename,
                ),
            )
        return artifact_id

    def artifact(self, artifact_id):
        with self.connection() as c:
            r = c.execute(
                "SELECT * FROM artifacts WHERE id=?", (artifact_id,)
            ).fetchone()
        if not r:
            raise BoltzFixError("NOT_FOUND", "Artifact not found")
        p = self.root / r["path"]
        if p.is_symlink() or not p.resolve().is_relative_to(self.root / "runs"):
            raise BoltzFixError("ARTIFACT_ACCESS_DENIED", "Invalid artifact location")
        if digest(p.read_bytes()) != r["sha256"]:
            raise BoltzFixError(
                "ARTIFACT_TAMPERED", "Artifact no longer matches its recorded hash"
            )
        return dict(r), p

    def add_candidate(self, candidate):
        with self.connection(immediate=True) as c:
            if c.execute(
                "SELECT selection FROM runs WHERE id=?", (candidate.run_id,)
            ).fetchone()[0]:
                raise BoltzFixError(
                    "SELECTION_FROZEN", "Selection has already been committed"
                )
            c.execute(
                "INSERT INTO candidates VALUES(?,?,?)",
                (candidate.id, candidate.run_id, candidate.model_dump_json()),
            )
            self.event(
                candidate.run_id,
                "candidate_created",
                {
                    "candidate_id": candidate.id,
                    "parent_id": candidate.parent_id,
                    "kind": candidate.kind,
                    "physical_valid": candidate.diagnostics.physical_valid,
                },
                c,
            )

    def candidates(self, run_id):
        with self.connection() as c:
            return [
                Candidate.model_validate_json(r[0])
                for r in c.execute(
                    "SELECT data FROM candidates WHERE run_id=? ORDER BY rowid",
                    (run_id,),
                )
            ]

    def candidate(self, candidate_id):
        with self.connection() as c:
            r = c.execute(
                "SELECT data FROM candidates WHERE id=?", (candidate_id,)
            ).fetchone()
        if not r:
            raise BoltzFixError("NOT_FOUND", "Candidate not found")
        return Candidate.model_validate_json(r[0])

    def freeze(self, run_id, receipt):
        with self.connection(immediate=True) as c:
            run = c.execute(
                "SELECT config_hash FROM runs WHERE id=?", (run_id,)
            ).fetchone()
            if not run or receipt.config_hash != run[0]:
                raise BoltzFixError(
                    "RECEIPT_INVALID", "Selection configuration does not match run"
                )
            if receipt.candidate_id:
                row = c.execute(
                    "SELECT data FROM candidates WHERE id=? AND run_id=?",
                    (receipt.candidate_id, run_id),
                ).fetchone()
                if not row:
                    raise BoltzFixError(
                        "RECEIPT_INVALID", "Selection does not belong to run"
                    )
                candidate = Candidate.model_validate_json(row[0])
                hashes = {
                    key: self.artifact(value)[0]["sha256"]
                    for key, value in candidate.artifact_ids.items()
                }
                if hashes != receipt.artifact_hashes:
                    raise BoltzFixError(
                        "RECEIPT_INVALID", "Selection artifacts do not match receipt"
                    )
            elif receipt.artifact_hashes:
                raise BoltzFixError(
                    "RECEIPT_INVALID", "No-candidate receipt must have empty artifacts"
                )
            updated = c.execute(
                "UPDATE runs SET selection=?,updated=? WHERE id=? AND selection IS NULL",
                (receipt.model_dump_json(), utcnow(), run_id),
            ).rowcount
            if not updated:
                raise BoltzFixError("SELECTION_FROZEN", "Selection is immutable")
            self.event(run_id, "selection_frozen", receipt.model_dump(), c)

    def summary(self, run_id):
        r = self.row(run_id)
        return RunSummary(
            id=run_id,
            status=r["status"],
            config=RunConfig.model_validate_json(r["config"]),
            config_hash=r["config_hash"],
            synthetic=json.loads(r["config"])["provider"] == "mock",
            created_at=r["created"],
            updated_at=r["updated"],
            capabilities=json.loads(r["capabilities"]),
            candidates=self.candidates(run_id),
            budget=self.ledger(run_id),
            selection=(
                SelectionReceipt.model_validate_json(r["selection"])
                if r["selection"]
                else None
            ),
            error=json.loads(r["error"]) if r["error"] else None,
        )

    def export_events(self, run_id):
        folder = self.root / "runs" / run_id
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "events.jsonl").write_text(
            "".join(json.dumps(e) + "\n" for e in self.events(run_id))
        )
