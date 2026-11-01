import numpy as np
import pytest
from boltzfix.providers.hosted import NvidiaProvider, choose_provider
from boltzfix.errors import BoltzFixError
from boltzfix.refinement import add_restraints, PROTOCOL


def test_hosted_payload_is_frozen_and_no_reference_fields(config):
    payload = NvidiaProvider.payload(config, 2, ccd="AIN")
    assert (
        payload["diffusion_samples"] == 2
        and payload["sampling_steps"] == 50
        and payload["recycling_steps"] == 3
    )
    assert (
        payload["ligands"][0]["predict_affinity"] is False
        and payload["constraints"] == []
    )
    assert "structural_templates" not in payload["polymers"][0]
    assert payload["ligands"] == [{"id": "L", "ccd": "AIN", "predict_affinity": False}]
    assert "smiles" not in payload["ligands"][0]


def test_confidence_mapping_and_malformed_response_rejected():
    with pytest.raises(BoltzFixError):
        NvidiaProvider.parse({"structures": [{}], "confidence_scores": []}, 1)
    data = {
        "structures": [{"structure": "data_x", "format": "mmcif"}],
        "confidence_scores": [0.7],
    }
    assert NvidiaProvider.parse(data, 1)[0].confidence == 0.7


@pytest.mark.asyncio
async def test_no_credentials_never_falls_back_to_paid_or_hardware(config, monkeypatch):
    monkeypatch.delenv("BOLTZFIX_ALLOW_LIVE", raising=False)
    assert not (await NvidiaProvider().capabilities()).available
    with pytest.raises(BoltzFixError):
        await NvidiaProvider().submit_prediction(config, 1)
    with pytest.raises(BoltzFixError):
        await choose_provider("auto")


@pytest.mark.asyncio
async def test_failed_ligand_preflight_prevents_dispatch(config, monkeypatch):
    monkeypatch.setenv("BOLTZFIX_ALLOW_LIVE", "1")
    monkeypatch.setenv("BOLTZFIX_SPONSOR_CONFIRMED", "1")
    monkeypatch.setenv(
        "BOLTZFIX_NVIDIA_PREFLIGHT_BLOCK", "incomplete_ligand_coordinates"
    )
    assert not (await NvidiaProvider().capabilities()).available
    with pytest.raises(BoltzFixError, match="omitted required ligand"):
        await NvidiaProvider().submit_prediction(config, 1)


def test_cpu_restraints_select_heavy_atoms_and_correct_units():
    from openmm import app, unit, System, Context, VerletIntegrator, Platform

    top = app.Topology()
    chain = top.addChain()
    protein = top.addResidue("ALA", chain)
    ligand = top.addResidue("LIG", chain)
    water = top.addResidue("HOH", chain)
    for name, element, residue in [
        ("CA", app.element.carbon, protein),
        ("H", app.element.hydrogen, protein),
        ("C1", app.element.carbon, ligand),
        ("O", app.element.oxygen, water),
    ]:
        top.addAtom(name, element, residue)
    system = System()
    for _ in range(4):
        system.addParticle(12)
    positions = np.zeros((4, 3)) * unit.nanometer
    assert add_restraints(system, top, positions) == {"protein": 1, "ligand": 1}
    integrator = VerletIntegrator(0.001)
    context = Context(
        system, integrator, Platform.getPlatformByName("CPU"), {"Threads": "2"}
    )
    moved = np.zeros((4, 3))
    moved[0, 0] = 0.1
    moved[2, 0] = 0.1
    context.setPositions(moved * unit.nanometer)
    energy = (
        context.getState(getEnergy=True)
        .getPotentialEnergy()
        .value_in_unit(unit.kilojoule_per_mole)
    )
    assert energy == pytest.approx(0.5 * (1000 + 10) * 0.1**2, rel=1e-6)
