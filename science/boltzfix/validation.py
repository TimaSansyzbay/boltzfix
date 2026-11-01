from __future__ import annotations
import importlib.metadata
import math
from pathlib import Path
import numpy as np
from rdkit import Chem
from .chemistry import (
    input_molecule,
    ligand_from_sdf,
    coordinates,
    graph_key,
    protein_atoms,
)
from .models import Check, DiagnosticReport


def diagnostics(
    protein_path,
    ligand_path,
    smiles,
    confidence=None,
    confidence_source="not_reported",
    inherited=False,
):
    checks = []
    contacts = []
    clashes = 0
    try:
        mol = ligand_from_sdf(ligand_path)
        expected = input_molecule(smiles)
        good = graph_key(mol) == graph_key(expected)
        checks.append(
            Check(
                id="identity.graph",
                status="pass" if good else "fail",
                category="identity",
                value=good,
                detail="Heavy-atom graph, charge and specified stereochemistry versus submitted SMILES",
            )
        )
        geometry = Chem.Mol(mol)
        Chem.AssignStereochemistryFrom3D(geometry, replaceExistingTags=True)
        stereo = graph_key(geometry) == graph_key(expected)
        checks.append(
            Check(
                id="identity.stereochemistry_3d",
                status="pass" if stereo else "fail",
                category="identity",
                value=stereo,
                detail="3D stereochemistry; graph tags alone do not establish coordinate chirality",
            )
        )
        xyz = coordinates(mol)
        checks.append(
            Check(id="identity.finite", status="pass", category="identity", value=True)
        )
    except Exception as exc:
        checks.append(
            Check(
                id="identity.load", status="fail", category="identity", detail=str(exc)
            )
        )
        return DiagnosticReport(
            checks=checks,
            chemically_eligible=False,
            physical_valid=False,
            failed_physical=0,
            severe_clashes=0,
        )
    try:
        atoms = protein_atoms(protein_path)
        px = np.array([a["xyz"] for a in atoms])
        distance = np.linalg.norm(xyz[:, None, :] - px[None, :, :], axis=2)
        table = Chem.GetPeriodicTable()
        lr = np.array([table.GetRvdw(a.GetAtomicNum()) for a in mol.GetAtoms()])
        pr = np.array([table.GetRvdw(a["element"]) for a in atoms])
        mask = distance < 0.65 * (lr[:, None] + pr[None, :])
        pairs = np.argwhere(mask)
        clashes = len(pairs)
        for i, j in sorted(pairs, key=lambda p: distance[p[0], p[1]])[:20]:
            contacts.append(
                {
                    "ligand_atom": int(i),
                    "protein_atom": atoms[j]["atom"],
                    "residue": atoms[j]["residue"],
                    "chain": atoms[j]["chain"],
                    "distance_angstrom": float(distance[i, j]),
                    "ligand_xyz": xyz[i].tolist(),
                    "protein_xyz": px[j].tolist(),
                }
            )
        checks.append(
            Check(
                id="environment.severe_clashes",
                status="pass" if clashes == 0 else "fail",
                category="environment",
                value=clashes,
                detail="Heavy-atom distances below 0.65 times summed van der Waals radii",
            )
        )
    except Exception as exc:
        checks.append(
            Check(
                id="environment.contacts",
                status="tool_error",
                category="environment",
                detail=str(exc),
            )
        )
    versions = {}
    try:
        from posebusters import PoseBusters

        versions["posebusters"] = importlib.metadata.version("posebusters")
        versions["rdkit"] = importlib.metadata.version("rdkit")
        # dock mode has no experimental ligand input. No reference coordinates enter here.
        report = PoseBusters(config="dock", max_workers=0).bust(
            mol_pred=mol, mol_cond=Path(protein_path), full_report=False
        )
        row = report.iloc[0]
        for name, value in row.items():
            if name in {"mol_pred", "mol_true", "mol_cond"}:
                continue
            if isinstance(value, (bool, np.bool_)):
                status = "pass" if bool(value) else "fail"
                checks.append(
                    Check(
                        id=f"posebusters.{name}",
                        status=status,
                        category=(
                            "environment"
                            if any(
                                s in name.lower()
                                for s in ["protein", "cofactor", "water"]
                            )
                            else "geometry"
                        ),
                        value=bool(value),
                    )
                )
            elif value is None or (isinstance(value, float) and math.isnan(value)):
                checks.append(
                    Check(
                        id=f"posebusters.{name}", status="unknown", category="geometry"
                    )
                )
        if not any(c.id.startswith("posebusters.") for c in checks):
            checks.append(
                Check(
                    id="posebusters.execution",
                    status="tool_error",
                    category="tool",
                    detail="No Boolean check results returned",
                )
            )
    except Exception as exc:
        checks.append(
            Check(
                id="posebusters.execution",
                status="tool_error",
                category="tool",
                detail=f"{type(exc).__name__}: {exc}",
            )
        )
    chemical = all(c.status == "pass" for c in checks if c.category == "identity")
    physical = [
        c
        for c in checks
        if c.category in {"geometry", "environment", "tool", "protocol"}
    ]
    failed = sum(c.status == "fail" for c in physical)
    valid = (
        chemical
        and bool(physical)
        and all(c.status in {"pass", "not_applicable"} for c in physical)
    )
    return DiagnosticReport(
        checks=checks,
        chemically_eligible=chemical,
        physical_valid=valid,
        failed_physical=failed,
        severe_clashes=clashes,
        confidence=confidence,
        confidence_source=confidence_source,
        inherited_confidence=inherited,
        closest_contacts=contacts,
        tool_versions=versions,
    )


def select_candidate(candidates):
    eligible = [
        c
        for c in candidates
        if c.diagnostics.chemically_eligible
        and not any(
            x.id == "protocol.local_displacement" and x.status == "fail"
            for x in c.diagnostics.checks
        )
    ]
    if not eligible:
        return None
    return min(
        eligible,
        key=lambda c: (
            not c.diagnostics.physical_valid,
            c.diagnostics.failed_physical,
            c.diagnostics.severe_clashes,
            -(c.diagnostics.confidence if c.diagnostics.confidence is not None else -1),
            c.id,
        ),
    )
