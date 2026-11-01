"""CPU restrained minimization. Subprocess boundary covers preparation and mechanics."""

from __future__ import annotations
import asyncio
import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
import time
import numpy as np
from rdkit import Chem
from .chemistry import (
    ligand_from_sdf,
    coordinates,
    set_coordinates,
    sdf_text,
    compare_poses,
    graph_key,
)
from .errors import BoltzFixError

PROTOCOL = {
    "protein_force_field": "amber14/protein.ff14SB.xml",
    "water_force_field": "amber14/tip3p.xml",
    "ligand_force_field": "openff-2.2.1.offxml",
    "charge_method": "am1bcc",
    "pH": 7.0,
    "solvent_padding_nm": 1.0,
    "ionic_strength_molar": 0.15,
    "cutoff_nm": 1.0,
    "protein_restraint_k": 1000.0,
    "ligand_restraint_k": 10.0,
    "iterations": 200,
    "tolerance_kj_mol_nm": 10.0,
    "platform": "CPU",
    "threads": 2,
}


def availability():
    external = os.getenv("BOLTZFIX_PHYSICS_PYTHON")
    if external and Path(external).resolve() != Path(sys.executable).resolve():
        import subprocess

        try:
            response = subprocess.run(
                [
                    external,
                    "-c",
                    "import importlib.util,json; print(json.dumps([m for m in ['openmm','openmmforcefields','openff.toolkit','pdbfixer'] if importlib.util.find_spec(m) is None]))",
                ],
                capture_output=True,
                text=True,
                timeout=10,
            )
            missing = (
                json.loads(response.stdout)
                if response.returncode == 0
                else ["external_environment_probe_failed"]
            )
            return {
                "available": not missing,
                "missing": missing,
                "protocol": PROTOCOL,
                "note": "External CPU physics environment; AmberTools checked at execution",
            }
        except Exception:
            return {
                "available": False,
                "missing": ["external_environment_probe_failed"],
                "protocol": PROTOCOL,
            }
    missing = [
        m for m in ["openmm", "openmmforcefields", "openff.toolkit"] if not _find(m)
    ]
    if not _find("pdbfixer"):
        missing.append("pdbfixer")
    return {
        "available": not missing,
        "missing": missing,
        "protocol": PROTOCOL,
        "note": "AM1-BCC additionally requires AmberTools; verified at execution",
    }


def _find(module):
    try:
        return importlib.util.find_spec(module) is not None
    except ModuleNotFoundError:
        return False


def add_restraints(system, topology, positions, ligand_residue="LIG"):
    from openmm import CustomExternalForce, unit

    force = CustomExternalForce("0.5*k*((x-x0)^2+(y-y0)^2+(z-z0)^2)")
    for name in ["k", "x0", "y0", "z0"]:
        force.addPerParticleParameter(name)
    xyz = positions.value_in_unit(unit.nanometer)
    counts = {"protein": 0, "ligand": 0}
    from .chemistry import STANDARD

    for atom in topology.atoms():
        if atom.element is None or atom.element.symbol == "H":
            continue
        kind = (
            "ligand"
            if atom.residue.name == ligand_residue
            else "protein" if atom.residue.name in STANDARD else None
        )
        if kind:
            force.addParticle(
                atom.index, [PROTOCOL[f"{kind}_restraint_k"], *xyz[atom.index]]
            )
            counts[kind] += 1
    system.addForce(force)
    return counts


def minimize_files(protein_path, ligand_path, output_dir):
    """Real preparation and mechanics; called in an isolated worker process."""
    # AmberTools discovery uses PATH, not the Python executable's location.
    os.environ["PATH"] = (
        str(Path(sys.executable).parent) + os.pathsep + os.environ.get("PATH", "")
    )
    from openmm import app, unit, Platform, VerletIntegrator
    from openff.toolkit import Molecule
    from openff.toolkit.utils.toolkits import AmberToolsToolkitWrapper
    from openmmforcefields.generators import SMIRNOFFTemplateGenerator
    from pdbfixer import PDBFixer
    import gemmi

    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    mol = ligand_from_sdf(ligand_path)
    off = Molecule.from_rdkit(
        Chem.AddHs(mol, addCoords=True), allow_undefined_stereo=False
    )
    off.assign_partial_charges("am1bcc", toolkit_registry=AmberToolsToolkitWrapper())
    fixer = PDBFixer(filename=str(protein_path))
    fixer.findMissingResidues()
    # No invention of missing sequence segments. Missing heavy atoms in existing
    # residues may be repaired; all additions remain in the preparation record.
    if fixer.missingResidues:
        raise BoltzFixError(
            "PREPARATION_UNSUPPORTED",
            "Missing residues require an explicit preparation protocol",
        )
    fixer.findNonstandardResidues()
    if fixer.nonstandardResidues:
        raise BoltzFixError("PREPARATION_UNSUPPORTED", "Unsupported residue chemistry")
    fixer.findMissingAtoms()
    added = {str(k): [a.name for a in v] for k, v in fixer.missingAtoms.items()}
    fixer.addMissingAtoms()
    fixer.addMissingHydrogens(PROTOCOL["pH"])
    modeller = app.Modeller(fixer.topology, fixer.positions)
    lt = off.to_topology().to_openmm()
    for residue in lt.residues():
        residue.name = "LIG"
    for atom in lt.atoms():
        atom.name = f"{atom.element.symbol}{atom.index+1}"
    ligand_positions = off.conformers[0].to_openmm()
    modeller.add(lt, ligand_positions)
    ff = app.ForceField(PROTOCOL["protein_force_field"], PROTOCOL["water_force_field"])
    generator = SMIRNOFFTemplateGenerator(
        molecules=[off], forcefield=PROTOCOL["ligand_force_field"]
    )
    ff.registerTemplateGenerator(generator.generator)
    modeller.addSolvent(
        ff,
        model="tip3p",
        padding=PROTOCOL["solvent_padding_nm"] * unit.nanometer,
        ionicStrength=PROTOCOL["ionic_strength_molar"] * unit.molar,
    )
    system = ff.createSystem(
        modeller.topology,
        nonbondedMethod=app.PME,
        nonbondedCutoff=PROTOCOL["cutoff_nm"] * unit.nanometer,
        constraints=app.HBonds,
    )
    counts = add_restraints(system, modeller.topology, modeller.positions)
    # Preserve the exact prepared system and initial positions, including solvent,
    # so reproduction does not depend on re-running stochastic preparation.
    from openmm import XmlSerializer
    import zipfile
    import io

    initial_pdb = io.StringIO()
    app.PDBFile.writeFile(
        modeller.topology, modeller.positions, initial_pdb, keepIds=True
    )
    with zipfile.ZipFile(out / "preparation.zip", "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("system.xml", XmlSerializer.serialize(system))
        archive.writestr("initial.pdb", initial_pdb.getvalue())
        buffer = io.BytesIO()
        np.save(buffer, np.asarray(modeller.positions.value_in_unit(unit.nanometer)))
        archive.writestr("positions_nm.npy", buffer.getvalue())
        archive.writestr("protocol.json", json.dumps(PROTOCOL, indent=2))
    integrator = VerletIntegrator(0.001 * unit.picoseconds)
    simulation = app.Simulation(
        modeller.topology,
        system,
        integrator,
        Platform.getPlatformByName("CPU"),
        {"Threads": "2"},
    )
    simulation.context.setPositions(modeller.positions)
    initial = (
        simulation.context.getState(getEnergy=True)
        .getPotentialEnergy()
        .value_in_unit(unit.kilojoule_per_mole)
    )
    simulation.minimizeEnergy(
        tolerance=PROTOCOL["tolerance_kj_mol_nm"]
        * unit.kilojoule_per_mole
        / unit.nanometer,
        maxIterations=PROTOCOL["iterations"],
    )
    state = simulation.context.getState(getEnergy=True, getPositions=True)
    final = state.getPotentialEnergy().value_in_unit(unit.kilojoule_per_mole)
    if not np.isfinite([initial, final]).all():
        raise BoltzFixError("MINIMIZATION_FAILED", "Nonfinite system energy")
    xyz = np.asarray(state.getPositions(asNumpy=True).value_in_unit(unit.angstrom))
    ligand_atoms = [
        a
        for a in modeller.topology.atoms()
        if a.residue.name == "LIG" and a.element.symbol != "H"
    ]
    if len(ligand_atoms) != mol.GetNumAtoms():
        raise BoltzFixError("MAPPING_ERROR", "Refinement changed ligand atom count")
    # OpenFF preserves the RDKit atom graph order; validate element correspondence.
    if [a.element.symbol for a in ligand_atoms] != [
        a.GetSymbol() for a in mol.GetAtoms()
    ]:
        raise BoltzFixError("MAPPING_ERROR", "Refinement heavy-atom order changed")
    refined = set_coordinates(mol, xyz[[a.index for a in ligand_atoms]])
    if graph_key(refined) != graph_key(mol):
        raise BoltzFixError("MAPPING_ERROR", "Refinement changed ligand identity")
    (out / "ligand.sdf").write_text(sdf_text(refined))
    # Export protein heavy atoms only, preserving the prepared topology's residue IDs.
    with (out / "solvated.pdb").open("w") as f:
        app.PDBFile.writeFile(modeller.topology, state.getPositions(), f, keepIds=True)
    st = gemmi.read_structure(str(out / "solvated.pdb"))
    from .chemistry import STANDARD

    for chain in st[0]:
        for i in reversed(range(len(chain))):
            if chain[i].name not in STANDARD:
                del chain[i]
            else:
                for j in reversed(range(len(chain[i]))):
                    if chain[i][j].element.is_hydrogen:
                        del chain[i][j]
    st.remove_empty_chains()
    st.write_pdb(str(out / "protein.pdb"))
    displacement = compare_poses(
        out / "protein.pdb", out / "ligand.sdf", protein_path, ligand_path
    )
    report = {
        "protocol": PROTOCOL,
        "initial_energy_kj_mol": initial,
        "final_energy_kj_mol": final,
        "same_system_energy": True,
        "restraint_counts": counts,
        "added_missing_atoms": added,
        "displacement": displacement,
        "local_protocol": displacement["protein_ca_rmsd"] <= 0.5
        and displacement["ligand_rmsd"] <= 2.0,
        "interpretation": "Minimization, not MD; energies are not comparable across independently built systems",
    }
    (out / "physics.json").write_text(json.dumps(report, indent=2))
    return report


async def refine(protein_path, ligand_path, timeout=300):
    executable = os.getenv("BOLTZFIX_PHYSICS_PYTHON", sys.executable)
    with tempfile.TemporaryDirectory(prefix="boltzfix-min-") as folder:
        start = time.monotonic()
        process = await asyncio.create_subprocess_exec(
            executable,
            "-m",
            "boltzfix.refinement",
            str(protein_path),
            str(ligand_path),
            folder,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            start_new_session=True,
        )
        try:
            stdout, stderr = await asyncio.wait_for(process.communicate(), timeout)
        except (asyncio.TimeoutError, asyncio.CancelledError) as exc:
            import signal

            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            await process.wait()
            if isinstance(exc, asyncio.CancelledError):
                raise
            raise BoltzFixError(
                "MINIMIZATION_TIMEOUT",
                "Preparation/minimization exceeded its wall-time limit",
            ) from exc
        if process.returncode:
            # Do not leak worker environment values through unbounded tracebacks.
            try:
                message = json.loads(stdout.decode()).get("error", "Worker failed")
            except (ValueError, UnicodeDecodeError):
                message = "Physics environment unavailable or preparation failed"
            raise BoltzFixError("MINIMIZATION_UNAVAILABLE", message[:1500])
        out = Path(folder)
        report = json.loads((out / "physics.json").read_text())
        report["latency_seconds"] = time.monotonic() - start
        import base64

        report["_preparation_bundle"] = base64.b64encode(
            (out / "preparation.zip").read_bytes()
        ).decode("ascii")
        return (
            (out / "protein.pdb").read_text(),
            (out / "ligand.sdf").read_text(),
            report,
        )


if __name__ == "__main__":
    try:
        minimize_files(*sys.argv[1:4])
    except Exception as exc:
        print(json.dumps({"error": f"{type(exc).__name__}: {exc}"}))
        sys.exit(1)
