"""Curate a candidate inventory without observing prediction outcomes.

Inputs are locally supplied metadata and structures; this module never retrieves
reference structures into the application artifact store.
"""

from __future__ import annotations
from datetime import date
import json
from pathlib import Path
import random
from Bio.Align import PairwiseAligner
from rdkit.Chem import AllChem, rdFingerprintGenerator
from rdkit import DataStructs
from .chemistry import input_molecule, ligand_from_sdf, graph_key, ca_sequence
from .errors import BoltzFixError
from .models import digest


def curate(inventory_path, output_dir, dev_count=6, eval_count=20, seed=20261031):
    inventory_path = Path(inventory_path).resolve()
    base = inventory_path.parent
    records = json.loads(inventory_path.read_text())
    accepted = []
    excluded = []
    ids = set()
    for row in records:
        case_id = row.get("case_id", "")
        try:
            if not case_id or case_id in ids:
                raise ValueError("Missing/duplicate case ID")
            ids.add(case_id)
            if date.fromisoformat(row["release_date"]) <= date(2023, 6, 1):
                raise ValueError("Not after structural training cutoff")
            if (
                row["method"] != "X-RAY DIFFRACTION"
                or not 0 < float(row["resolution"]) <= 2.5
            ):
                raise ValueError("Method/resolution ineligible")
            required = [
                "complete_ligand",
                "complete_binding_site",
                "single_ligand_pose",
                "noncovalent",
                "no_essential_cofactor",
                "single_chain",
            ]
            if not all(row.get(k) is True for k in required):
                raise ValueError("Unverified structural eligibility")
            sequence = row["sequence"]
            if not 100 <= len(sequence) <= 400 or set(sequence) - set(
                "ACDEFGHIKLMNPQRSTVWY"
            ):
                raise ValueError("Protein scope")
            expected = input_molecule(row["ligand_smiles"])
            p = (base / row["reference_protein"]).resolve()
            l = (base / row["reference_ligand"]).resolve()
            if graph_key(ligand_from_sdf(l)) != graph_key(expected):
                raise ValueError("Reference ligand identity differs")
            seq, _ = ca_sequence(p)
            if seq != sequence:
                raise ValueError("Reference protein sequence differs")
            if not row.get("training_similarity_stratum"):
                raise ValueError("Training-similarity stratum required")
            accepted.append(
                {
                    **row,
                    "reference_protein": str(p),
                    "reference_ligand": str(l),
                    "reference_hashes": {
                        "protein": digest(p.read_bytes()),
                        "ligand": digest(l.read_bytes()),
                    },
                }
            )
        except Exception as exc:
            excluded.append({"case_id": case_id, "reason": str(exc)})
    parent = list(range(len(accepted)))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    generator = rdFingerprintGenerator.GetMorganGenerator(radius=2, fpSize=2048)
    fingerprints = [
        generator.GetFingerprint(input_molecule(r["ligand_smiles"])) for r in accepted
    ]
    aligner = PairwiseAligner()
    aligner.match_score = 2
    aligner.mismatch_score = -1
    aligner.open_gap_score = -3
    aligner.extend_gap_score = -0.5
    for i in range(len(accepted)):
        for j in range(i):
            sa, sb = accepted[i]["sequence"], accepted[j]["sequence"]
            a = aligner.align(sa, sb)[0]
            matched = identical = 0
            for (x, y), (u, v) in zip(*a.aligned):
                matched += int(y - x)
                identical += sum(c == d for c, d in zip(sa[x:y], sb[u:v]))
            close_protein = (
                matched / max(len(sa), len(sb)) >= 0.8
                and identical / max(1, matched) >= 0.5
            )
            close_ligand = (
                DataStructs.TanimotoSimilarity(fingerprints[i], fingerprints[j]) >= 0.8
            )
            if close_protein or close_ligand:
                parent[find(i)] = find(j)
    groups = {}
    for i, r in enumerate(accepted):
        groups.setdefault(find(i), []).append(r)
    group_list = list(groups.values())
    random.Random(seed).shuffle(group_list)
    # Whole connected groups are assigned together, even when that limits exact counts.
    dev = []
    evaluation = []
    unused = []
    for group in group_list:
        if len(dev) + len(group) <= dev_count:
            dev.extend(group)
        elif len(evaluation) + len(group) <= eval_count:
            evaluation.extend(group)
        else:
            unused.extend(group)
    if len(dev) != dev_count or len(evaluation) != eval_count:
        raise BoltzFixError(
            "COHORT_INSUFFICIENT",
            f"Eligible independent groups supply dev={len(dev)}/{dev_count}, eval={len(evaluation)}/{eval_count}. Exclusions: {excluded}. Do not relax after observing predictions.",
        )
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    private = {
        "seed": seed,
        "inventory_hash": digest(inventory_path.read_bytes()),
        "development": dev,
        "evaluation": evaluation,
        "unused": unused,
        "excluded": excluded,
        "cutoff": "2023-06-01",
        "grouping": "protein identity ≥0.5 with ≥0.8 coverage of longer sequence OR Morgan Tanimoto ≥0.8",
    }
    if (out / "reference-manifest.json").exists():
        raise BoltzFixError(
            "MANIFEST_EXISTS", "Use a new directory to preserve the frozen cohort"
        )
    (out / "reference-manifest.json").write_text(json.dumps(private, indent=2))
    public = {
        "seed": seed,
        "source_manifest_hash": digest(private),
        "development": [],
        "evaluation": [],
    }
    for partition in ["development", "evaluation"]:
        for i, r in enumerate(private[partition]):
            public[partition].append(
                {
                    "case_id": f"{partition[:3]}_{i+1:03d}",
                    "sequence": r["sequence"],
                    "ligand_smiles": r["ligand_smiles"],
                    "msa": r.get("msa"),
                    "training_similarity_stratum": r["training_similarity_stratum"],
                }
            )
            private[partition][i]["anonymous_id"] = public[partition][-1]["case_id"]
    (out / "reference-manifest.json").write_text(json.dumps(private, indent=2))
    public["source_manifest_hash"] = digest(private)
    (out / "public-manifest.json").write_text(json.dumps(public, indent=2))
    return {
        "development": len(dev),
        "evaluation": len(evaluation),
        "excluded": excluded,
        "strata": {
            s: sum(r["training_similarity_stratum"] == s for r in evaluation)
            for s in {r["training_similarity_stratum"] for r in evaluation}
        },
    }
