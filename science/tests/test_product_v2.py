import asyncio
import json
from pathlib import Path
import pytest
from fastapi.testclient import TestClient
from boltzfix.api import create_app
from boltzfix.engine import Engine
from boltzfix.models import RunConfig
from boltzfix.providers.diffdock import DiffDockProvider, ENDPOINT
from boltzfix.errors import BoltzFixError
from boltzfix.structure_io import protein_input, prepare_pair

EXAMPLES = Path(__file__).resolve().parents[1] / "fixtures" / "examples"


def example(name="brd4"):
    return json.loads((EXAMPLES / (name + ".json")).read_text())


def test_docking_complete_chemistry_and_fixed_endpoint():
    e = example()
    p, seq = protein_input(e["protein_pdb"])
    config = RunConfig(
        sequence=seq,
        ligand_smiles=e["ligand_smiles"],
        protein_pdb=p,
        provider="nvidia_diffdock",
        protocol_version="boltzfix-v2",
        initial_samples=2,
        policy="initial",
    )
    payload = DiffDockProvider.payload(config, 2)
    assert ENDPOINT.endswith("/biology/mit/diffdock")
    assert all(line.startswith("ATOM") for line in payload["protein"].splitlines())
    assert payload["num_poses"] == 2 and not payload["save_trajectory"]
    parsed = DiffDockProvider.parse(
        {
            "status": "success",
            "ligand_positions": [e["ligand_sdf"]] * 2,
            "position_confidence": [-3.5, -4.0],
        },
        config,
        2,
    )
    assert len(parsed) == 2 and parsed[0].confidence == -3.5
    assert "uncalibrated" in parsed[0].metadata["confidence_scale"]
    from rdkit import Chem

    # The validated SDF retains the submitted chlorine, stereochemistry and atom graph.
    _, sdf, _, _ = prepare_pair(p, e["ligand_sdf"], e["ligand_smiles"])
    mol = Chem.MolFromMolBlock(sdf, removeHs=True)
    assert mol.GetNumHeavyAtoms() == 31
    assert any(a.GetSymbol() == "Cl" for a in mol.GetAtoms())
    with pytest.raises(BoltzFixError, match="count"):
        DiffDockProvider.parse(
            {"status": "success", "ligand_positions": [], "position_confidence": []},
            config,
            2,
        )


def test_import_real_multisdf_report_and_rejection(tmp_path):
    e = example("retinol")
    app = create_app(tmp_path / "app", False)
    settings = {
        "name": "<script>alert(1)</script>",
        "policy": "initial",
        "agent_mode": "synthetic",
        "budget": {
            "samples": 0,
            "prediction_requests": 0,
            "llm_calls": 0,
            "llm_tokens": 0,
        },
    }
    with TestClient(app) as client:
        assert client.get("/v1/examples/retinol").status_code == 200
        response = client.post(
            "/v1/runs/import",
            data={"smiles": e["ligand_smiles"], "settings": json.dumps(settings)},
            files=[
                ("protein", ("protein.pdb", e["protein_pdb"])),
                ("ligands", ("poses.sdf", e["ligand_sdf"] + e["ligand_sdf"])),
            ],
        )
        assert response.status_code == 202, response.text
        run = response.json()["id"]
        store = app.state.store
        store.claim(run, "test")
        asyncio.run(Engine(store).run(run))
        summary = store.summary(run)
        assert summary.status == "completed" and len(summary.candidates) == 2
        assert summary.budget.used.get("samples", 0) == 0
        assert summary.budget.used.get("llm_calls", 0) == 0
        assert summary.selection
        report = client.get(f"/v1/runs/{run}/report")
        assert report.status_code == 200 and "<script>" not in report.text
        assert "&lt;script&gt;" in report.text
        assert "experimental accuracy remains unverified" in report.text
        assert summary.config_hash in report.text
        assert client.get(f"/v1/runs/{run}/report?format=md").text.startswith(
            "# BoltzFix"
        )
        assert client.get(f"/v1/runs/{run}/report?format=bad").status_code == 422
        bad = client.post(
            "/v1/structures/protein", files={"protein": ("bad.pdb", "not a structure")}
        )
        assert bad.status_code == 422
