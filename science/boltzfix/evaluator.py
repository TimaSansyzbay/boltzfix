"""Offline only. Never imported by the API, agents or investigation engine."""

from __future__ import annotations
import json
from pathlib import Path
import numpy as np
from .chemistry import compare_poses
from .errors import BoltzFixError
from .models import SelectionReceipt, digest


def evaluate_run(store, run_id, reference_protein, reference_ligand, output_dir):
    summary = store.summary(run_id)
    if (
        summary.status not in {"completed", "partial", "failed", "cancelled"}
        or summary.selection is None
    ):
        raise BoltzFixError(
            "SELECTION_NOT_FROZEN",
            "Evaluation requires an ended investigation and a frozen selection",
        )
    receipt = summary.selection
    if receipt.config_hash != summary.config_hash:
        raise BoltzFixError("RECEIPT_INVALID", "Configuration hash differs")
    selected = store.candidate(receipt.candidate_id) if receipt.candidate_id else None
    if (
        selected
        and {
            k: store.artifact(v)[0]["sha256"] for k, v in selected.artifact_ids.items()
        }
        != receipt.artifact_hashes
    ):
        raise BoltzFixError(
            "RECEIPT_INVALID", "Candidate hashes differ from frozen selection"
        )
    scores = []
    for c in summary.candidates:
        try:
            p = store.artifact(c.artifact_ids["protein"])[1]
            l = store.artifact(c.artifact_ids["ligand"])[1]
            score = compare_poses(p, l, reference_protein, reference_ligand)
            score.update(
                {
                    "accuracy_success": score["ligand_rmsd"] < 2.0,
                    "physical_valid": c.diagnostics.physical_valid,
                    "primary_success": score["ligand_rmsd"] < 2.0
                    and c.diagnostics.physical_valid,
                }
            )
        except BoltzFixError as exc:
            score = {
                "error": exc.public(),
                "accuracy_success": False,
                "primary_success": False,
            }
        scores.append(
            {"candidate_id": c.id, "selected": c.id == receipt.candidate_id, **score}
        )
    final = next(
        (s for s in scores if s["selected"]),
        {
            "accuracy_success": False,
            "primary_success": False,
            "error": {"code": "NO_SELECTION"},
        },
    )
    output = {
        "schema_version": "evaluation-v1",
        "run_id": run_id,
        "case_id": summary.config.case_id,
        "policy": summary.config.policy,
        "synthetic": summary.synthetic,
        "selection_receipt_hash": digest(receipt.model_dump()),
        "reference_hashes": {
            "protein": digest(Path(reference_protein).read_bytes()),
            "ligand": digest(Path(reference_ligand).read_bytes()),
        },
        "selected": final,
        "search_success": any(s["primary_success"] for s in scores),
        "candidates": scores,
        "budget": summary.budget.model_dump(),
        "limitations": [
            "Pilot retrospective evaluation; pretrained exposure cannot be excluded.",
            "Validity uses the predicted receptor; native-receptor validity is not included.",
        ],
    }
    folder = Path(output_dir).resolve()
    if folder.is_relative_to(store.root):
        raise BoltzFixError(
            "REFERENCE_ISOLATION",
            "Evaluation output must be outside agent/application storage",
        )
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / f"{run_id}.evaluation.json"
    if target.exists():
        raise BoltzFixError(
            "EVALUATION_EXISTS",
            "Preserve existing evaluation; choose a new output directory",
        )
    target.write_text(json.dumps(output, indent=2))
    return output


def paired_bootstrap(left, right, seed=20261031, repeats=10000):
    """Inputs are one aggregated endpoint per independent complex, not per repeat."""
    x = np.asarray(left, dtype=float)
    y = np.asarray(right, dtype=float)
    if (
        x.shape != y.shape
        or x.ndim != 1
        or len(x) == 0
        or not np.isfinite(x).all()
        or not np.isfinite(y).all()
    ):
        raise BoltzFixError(
            "EVALUATION_INVALID", "Matched finite case-level arrays required"
        )
    differences = x - y
    rng = np.random.default_rng(seed)
    means = differences[rng.integers(0, len(x), (repeats, len(x)))].mean(1)
    low, high = np.quantile(means, [0.025, 0.975])
    return {
        "n_complexes": len(x),
        "difference": float(differences.mean()),
        "ci95": [float(low), float(high)],
        "bootstrap_resamples": repeats,
        "superiority_supported": bool(low > 0),
        "interpretation": "Case-level paired pilot interval; not proof of general drug-discovery benefit",
    }
