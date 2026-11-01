"""Run explicitly with BOLTZFIX_PHYSICS_PYTHON pointing at the conda physics env."""

import os
from pathlib import Path
import pytest
from boltzfix.refinement import refine
from boltzfix.chemistry import ligand_from_sdf, graph_key
from boltzfix.errors import BoltzFixError


@pytest.mark.asyncio
@pytest.mark.skipif(
    not os.getenv("BOLTZFIX_PHYSICS_PYTHON"),
    reason="Separate physics environment not configured",
)
async def test_genuine_cpu_minimization_preserves_chemistry(tmp_path):
    fixture = Path(__file__).resolve().parents[2] / "fixtures/physics"
    p, l, result = await refine(fixture / "protein.pdb", fixture / "ligand.sdf", 300)
    (tmp_path / "ligand.sdf").write_text(l)
    assert graph_key(ligand_from_sdf(tmp_path / "ligand.sdf")) == graph_key(
        ligand_from_sdf(fixture / "ligand.sdf")
    )
    assert (
        result["protocol"]["platform"] == "CPU"
        and result["restraint_counts"]["ligand"] == 13
    )
    assert result["final_energy_kj_mol"] <= result["initial_energy_kj_mol"]
    assert result["local_protocol"] and "ATOM" in p
    import base64, io, zipfile

    prepared = zipfile.ZipFile(
        io.BytesIO(base64.b64decode(result["_preparation_bundle"]))
    )
    assert {"system.xml", "positions_nm.npy", "initial.pdb", "protocol.json"} == set(
        prepared.namelist()
    )


@pytest.mark.asyncio
async def test_preparation_timeout_terminates_subprocess(structures):
    with pytest.raises(BoltzFixError, match="wall-time"):
        await refine(structures[0], structures[1], 0.0001)
