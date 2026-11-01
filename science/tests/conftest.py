from pathlib import Path
import pytest
from boltzfix.chemistry import synthetic_fixture
from boltzfix.models import RunConfig
from boltzfix.store import Store

SMILES = "CC(=O)Oc1ccccc1C(=O)O"


@pytest.fixture
def config():
    return RunConfig(sequence="A" * 100, ligand_smiles=SMILES)


@pytest.fixture
def store(tmp_path):
    return Store(tmp_path / "state")


@pytest.fixture
def structures(tmp_path):
    p, s, c, m = synthetic_fixture(SMILES, 1)
    protein = tmp_path / "protein.pdb"
    ligand = tmp_path / "ligand.sdf"
    protein.write_text(p)
    ligand.write_text(s)
    return protein, ligand, c, m
