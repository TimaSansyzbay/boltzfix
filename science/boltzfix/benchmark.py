from __future__ import annotations
import asyncio
import csv
import json
from pathlib import Path
import random
from .engine import Engine
from .errors import BoltzFixError
from .models import BudgetLimits, RunConfig, digest, utcnow
from .providers import choose_provider
from .providers.hosted import FROZEN_PARAMETERS
from .refinement import PROTOCOL
from .store import uid

POLICIES = [
    "initial",
    "fixed_resampling",
    "fixed_hybrid",
    "rules",
    "single_agent",
    "collective",
]


def register(
    public_manifest,
    output,
    provider="mock",
    hybrid_schedule="sample_then_minimize",
    model="claude-opus-5-5",
    partition="evaluation",
):
    manifest = json.loads(Path(public_manifest).read_text())
    cases = manifest[partition]
    if not cases:
        raise BoltzFixError("MANIFEST_INVALID", "No admitted cases")
    ids = [r["case_id"] for r in cases]
    if len(set(ids)) != len(ids):
        raise BoltzFixError("MANIFEST_INVALID", "Duplicate case IDs")
    for case in cases:
        if set(case) - {
            "case_id",
            "sequence",
            "ligand_smiles",
            "msa",
            "training_similarity_stratum",
        }:
            raise BoltzFixError(
                "REFERENCE_ISOLATION",
                "Public cases may contain only prediction inputs and similarity strata",
            )
        if provider != "mock" and not case.get("msa"):
            raise BoltzFixError(
                "MSA_REQUIRED",
                "Live comparisons require one frozen cached MSA per case",
            )
        RunConfig(sequence=case["sequence"], ligand_smiles=case["ligand_smiles"])
    record = {
        "schema_version": "campaign-v1",
        "registered_at": utcnow(),
        "provider": provider,
        "hybrid_schedule": hybrid_schedule,
        "llm_model": model,
        "partition": partition,
        "seed": 20261031,
        "policies": POLICIES,
        "cases": cases,
        "source_manifest_hash": digest(manifest),
        "prediction_parameters": FROZEN_PARAMETERS,
        "physics_parameters": PROTOCOL,
        "budget": BudgetLimits().model_dump(),
        "primary_endpoint": "ligand_RMSD<2A AND predicted-receptor physical validity",
        "source_confidence": (
            "synthetic_confidence" if provider == "mock" else "confidence_scores"
        ),
        "limitations": [
            "Hosted seed control and deployment version may be unavailable.",
            "A public retrospective cohort cannot prove absence of pretrained exposure.",
        ],
        "synthetic": provider == "mock",
    }
    target = Path(output)
    if target.exists():
        raise BoltzFixError(
            "PROTOCOL_EXISTS", "Registered campaign files are immutable; use a new path"
        )
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps({"record": record, "sha256": digest(record)}, indent=2)
    )
    return record


async def campaign(store, registration, output):
    registered = json.loads(Path(registration).read_text())
    record = registered["record"]
    if digest(record) != registered["sha256"]:
        raise BoltzFixError("PROTOCOL_TAMPERED", "Registration hash differs")
    if (
        record["prediction_parameters"] != FROZEN_PARAMETERS
        or record["physics_parameters"] != PROTOCOL
    ):
        raise BoltzFixError(
            "PROTOCOL_CHANGED", "Installed protocols differ from registration"
        )
    provider = await choose_provider(record["provider"])
    caps = await provider.capabilities()
    if not caps.available:
        raise BoltzFixError("PREFLIGHT_REQUIRED", caps.reason, pause=True)
    out = Path(output)
    out.mkdir(parents=True, exist_ok=True)
    journal = out / "campaign.jsonl"
    if journal.exists():
        raise BoltzFixError(
            "CAMPAIGN_EXISTS",
            "Campaign already started; reconcile existing jobs instead of automatic resubmission",
        )
    owner = uid("campaign")
    if not store.acquire_worker(owner):
        raise BoltzFixError(
            "WORKER_BUSY",
            "Stop the API scientific worker before starting a CLI campaign",
        )
    stop = asyncio.Event()

    async def heartbeats():
        while not stop.is_set():
            store.heartbeat(owner)
            try:
                await asyncio.wait_for(stop.wait(), 5)
            except asyncio.TimeoutError:
                pass

    heartbeat = asyncio.create_task(heartbeats())
    engine = Engine(store)
    rng = random.Random(record["seed"])
    rows = []
    try:
        for case in record["cases"]:
            # Exact common initial candidate is reused and logically charged to every policy.
            config = RunConfig(
                name=f"{case['case_id']} / initial",
                case_id=case["case_id"],
                sequence=case["sequence"],
                ligand_smiles=case["ligand_smiles"],
                msa=case.get("msa"),
                provider=provider.name,
                agent_mode="synthetic" if provider.name == "mock" else "live",
                policy="initial",
                budget=BudgetLimits.model_validate(record["budget"]),
                llm_model=record["llm_model"],
                hybrid_schedule=record["hybrid_schedule"],
            )
            initial = None
            policies = record["policies"][1:]
            rng.shuffle(policies)
            for policy in ["initial", *policies]:
                conf = config.model_copy(
                    update={"policy": policy, "name": f"{case['case_id']} / {policy}"}
                )
                run_id = store.create_run(conf)
                store.claim(run_id, owner)

                # Engine-local heartbeat also prevents stale recovery of long remote calls.
                async def run_heartbeat():
                    while True:
                        store.heartbeat(owner, run_id)
                        await asyncio.sleep(5)

                rh = asyncio.create_task(run_heartbeat())
                try:
                    await engine.run(run_id, initial)
                finally:
                    rh.cancel()
                    await asyncio.gather(rh, return_exceptions=True)
                summary = store.summary(run_id)
                row = {
                    "case_id": case["case_id"],
                    "policy": policy,
                    "run_id": run_id,
                    "status": summary.status,
                    "synthetic": summary.synthetic,
                    "registration_sha256": registered["sha256"],
                    "capabilities": summary.capabilities,
                }
                with journal.open("a") as f:
                    f.write(json.dumps(row) + "\n")
                rows.append(row)
                if summary.status == "paused":
                    return {"status": "paused", "rows": rows, "reason": summary.error}
                if policy == "initial":
                    initial = summary.candidates[0] if summary.candidates else None
                    if initial is None:
                        # Keep failed cases in analysis; do not replace admission or silently rerun.
                        for missing in policies:
                            omitted = {
                                "case_id": case["case_id"],
                                "policy": missing,
                                "run_id": None,
                                "status": "initial_failed",
                                "synthetic": summary.synthetic,
                            }
                            rows.append(omitted)
                            with journal.open("a") as f:
                                f.write(json.dumps(omitted) + "\n")
                        break
        return {"status": "completed", "rows": rows}
    finally:
        stop.set()
        await heartbeat
        with store.connection(immediate=True) as c:
            c.execute(
                "DELETE FROM locks WHERE name='scientific_worker' AND owner=?", (owner,)
            )


def analyze(evaluation_dir, output):
    """Aggregate frozen offline results with paired case-level uncertainty."""
    from .evaluator import paired_bootstrap

    results = [
        json.loads(p.read_text())
        for p in sorted(Path(evaluation_dir).glob("*.evaluation.json"))
    ]
    if not results:
        raise BoltzFixError("NO_EVALUATIONS", "Supply offline evaluation JSON files")
    keys = [(r["case_id"], r["policy"]) for r in results]
    if len(keys) != len(set(keys)):
        raise BoltzFixError(
            "DUPLICATE_CASE", "Aggregate independent repeats by case before analysis"
        )
    if len({r["synthetic"] for r in results}) != 1:
        raise BoltzFixError("MIXED_MODES", "Do not pool synthetic and live results")
    by_policy = {
        p: {r["case_id"]: r for r in results if r["policy"] == p}
        for p in {r["policy"] for r in results}
    }
    universe = set.union(*(set(v) for v in by_policy.values()))
    if any(set(v) != universe for v in by_policy.values()):
        raise BoltzFixError(
            "UNPAIRED_COHORT",
            "Every policy must include every admitted case, including failures",
        )
    comparisons = {}
    if "collective" in by_policy:
        cases = sorted(universe)
        for p in sorted(by_policy):
            if p == "collective":
                continue
            comparisons[p] = paired_bootstrap(
                [
                    by_policy["collective"][c]["selected"]["primary_success"]
                    for c in cases
                ],
                [by_policy[p][c]["selected"]["primary_success"] for c in cases],
            )
    out = Path(output)
    out.mkdir(parents=True, exist_ok=True)
    rows = []
    for r in results:
        rows.append(
            {
                "case_id": r["case_id"],
                "policy": r["policy"],
                "synthetic": r["synthetic"],
                "primary_success": r["selected"]["primary_success"],
                "accuracy_success": r["selected"]["accuracy_success"],
                "ligand_rmsd": r["selected"].get("ligand_rmsd"),
                "search_success": r["search_success"],
                "samples": r["budget"]["used"].get("samples", 0),
                "refinements": r["budget"]["used"].get("refinements", 0),
                "llm_calls": r["budget"]["used"].get("llm_calls", 0),
                "llm_tokens": r["budget"]["used"].get("llm_tokens", 0),
                "wall_seconds": r["budget"]["wall_seconds"],
            }
        )
    with (out / "benchmark.csv").open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    report = {
        "n_complexes": len(universe),
        "synthetic": results[0]["synthetic"],
        "comparisons_vs_collective": comparisons,
        "interpretation": (
            "Synthetic engineering exercise only"
            if results[0]["synthetic"]
            else "Pilot scientific comparison; report negative and uncertain results"
        ),
        "policies": {
            p: {
                "n": len(v),
                "primary_success_rate": sum(
                    r["selected"]["primary_success"] for r in v.values()
                )
                / len(v),
            }
            for p, v in by_policy.items()
        },
    }
    (out / "report.json").write_text(json.dumps(report, indent=2))
    return report


def evaluate_campaign(store, registration, journal_path, private_manifest, output):
    """Offline scoring of every registered case/policy, including failed admissions."""
    from .evaluator import evaluate_run

    registration = json.loads(Path(registration).read_text())
    record = registration["record"]
    if digest(record) != registration["sha256"]:
        raise BoltzFixError("PROTOCOL_TAMPERED", "Registration hash differs")
    refs = json.loads(Path(private_manifest).read_text())
    reference_by_id = {r["anonymous_id"]: r for r in refs[record["partition"]]}
    rows = [
        json.loads(line) for line in Path(journal_path).read_text().splitlines() if line
    ]
    key_rows = {(r["case_id"], r["policy"]): r for r in rows}
    expected = {(c["case_id"], p) for c in record["cases"] for p in record["policies"]}
    if set(key_rows) != expected or len(key_rows) != len(rows):
        raise BoltzFixError(
            "CAMPAIGN_INCOMPLETE",
            "Finish or explicitly close the complete campaign before scoring; missing cases cannot disappear",
        )
    folder = Path(output).resolve()
    if folder.is_relative_to(store.root):
        raise BoltzFixError(
            "REFERENCE_ISOLATION",
            "Evaluation output must remain outside application storage",
        )
    folder.mkdir(parents=True, exist_ok=True)
    for case in record["cases"]:
        reference = reference_by_id.get(case["case_id"])
        if not reference or any(
            reference[k] != case[k] for k in ["sequence", "ligand_smiles"]
        ):
            raise BoltzFixError(
                "MANIFEST_INVALID", "Reference mapping does not match admitted inputs"
            )
        for key in ["protein", "ligand"]:
            if (
                digest(Path(reference[f"reference_{key}"]).read_bytes())
                != reference["reference_hashes"][key]
            ):
                raise BoltzFixError(
                    "REFERENCE_TAMPERED", "Reference hash differs from curation"
                )
        for policy in record["policies"]:
            row = key_rows[(case["case_id"], policy)]
            if row["run_id"]:
                evaluate_run(
                    store,
                    row["run_id"],
                    reference["reference_protein"],
                    reference["reference_ligand"],
                    folder,
                )
            else:
                failure = {
                    "schema_version": "evaluation-v1",
                    "case_id": case["case_id"],
                    "policy": policy,
                    "synthetic": record["synthetic"],
                    "selected": {
                        "primary_success": False,
                        "accuracy_success": False,
                        "error": {"code": "INITIAL_FAILED"},
                    },
                    "search_success": False,
                    "budget": {"used": {}, "wall_seconds": 0},
                    "admitted_failure": True,
                }
                target = folder / f"{case['case_id']}-{policy}.evaluation.json"
                if target.exists():
                    raise BoltzFixError(
                        "EVALUATION_EXISTS", "Preserve existing offline evaluations"
                    )
                target.write_text(json.dumps(failure, indent=2))
    return {"scored_case_policy_pairs": len(expected)}
