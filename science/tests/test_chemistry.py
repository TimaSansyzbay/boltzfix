import numpy as np
import pytest
from rdkit import Chem
from boltzfix.chemistry import (
    extract_complex,
    ligand_from_sdf,
    input_molecule,
    coordinates,
    graph_key,
    set_coordinates,
    sdf_text,
    in_place_rmsd,
    compare_poses,
    kabsch,
    combine_complex,
)
from boltzfix.errors import BoltzFixError
from boltzfix.validation import diagnostics
from conftest import SMILES


def test_identity_and_coordinate_roundtrip(structures, tmp_path):
    protein, ligand, cif, mapping = structures
    p, s, m = extract_complex(cif, SMILES)
    out = tmp_path / "roundtrip.sdf"
    out.write_text(s)
    a, b = ligand_from_sdf(ligand), ligand_from_sdf(out)
    assert graph_key(a) == graph_key(b)
    assert in_place_rmsd(a, b) < 0.002
    assert len(m["atoms"]) == a.GetNumHeavyAtoms()


def test_atom_permutation_and_in_place_rmsd(structures):
    mol = ligand_from_sdf(structures[1])
    permuted = Chem.RenumberAtoms(mol, list(reversed(range(mol.GetNumAtoms()))))
    assert in_place_rmsd(mol, permuted) < 1e-6
    translated = set_coordinates(mol, coordinates(mol) + [3, 0, 0])
    assert in_place_rmsd(translated, mol) == pytest.approx(3, abs=1e-6)
    xyz = coordinates(mol)
    rotated = set_coordinates(mol, xyz.mean(0) + (xyz - xyz.mean(0)) * [-1, -1, 1])
    assert in_place_rmsd(rotated, mol) > 1


def test_refined_complex_export_preserves_coordinates_and_chemistry(
    structures, tmp_path
):
    p, l, _, mapping = structures
    cif = combine_complex(p.read_text(), l.read_text(), mapping)
    _, sdf, restored = extract_complex(cif, SMILES)
    output = tmp_path / "exported.sdf"
    output.write_text(sdf)
    assert in_place_rmsd(ligand_from_sdf(l), ligand_from_sdf(output)) < 0.002
    assert restored["connectivity_source"] == "cif_chem_comp_bond"


def test_global_protein_alignment_applies_to_ligand(structures, tmp_path):
    p, l, _, _ = structures
    assert compare_poses(p, l, p, l)["ligand_rmsd"] < 1e-6
    import gemmi

    rotation = np.array([[0, -1, 0], [1, 0, 0], [0, 0, 1]])
    translation = np.array([11, 7, -2])
    st = gemmi.read_structure(str(p))
    for chain in st[0]:
        for res in chain:
            for atom in res:
                xyz = (
                    np.array([atom.pos.x, atom.pos.y, atom.pos.z]) @ rotation
                    + translation
                )
                atom.pos = gemmi.Position(*xyz)
    pp = tmp_path / "shifted.pdb"
    st.write_pdb(str(pp))
    mol = ligand_from_sdf(l)
    ll = tmp_path / "shifted.sdf"
    ll.write_text(
        sdf_text(set_coordinates(mol, coordinates(mol) @ rotation + translation))
    )
    assert compare_poses(pp, ll, p, l)["ligand_rmsd"] < 0.002


def test_wrong_ligand_pose_is_not_fitted_away(structures, tmp_path):
    p, l, _, _ = structures
    mol = ligand_from_sdf(l)
    wrong = tmp_path / "wrong.sdf"
    wrong.write_text(sdf_text(set_coordinates(mol, coordinates(mol) + [4, 0, 0])))
    assert compare_poses(p, wrong, p, l)["ligand_rmsd"] == pytest.approx(4, abs=0.002)


def test_protein_only_provider_response_is_explicit_failure(structures):
    import gemmi

    protein = gemmi.read_structure(str(structures[0]))
    with pytest.raises(BoltzFixError, match="without a ligand"):
        extract_complex(protein.make_mmcif_document().as_string(), SMILES)


@pytest.mark.parametrize(
    "smiles", ["C", "CC.CCCC", "[Na+]", "CC(O)c1ccccc1CCC", "not smiles"]
)
def test_unsupported_inputs_rejected(smiles):
    with pytest.raises(BoltzFixError):
        input_molecule(smiles)


def test_distorted_ring_and_clashes_are_measured(structures, tmp_path):
    p, l, _, _ = structures
    mol = ligand_from_sdf(l)
    good = diagnostics(p, l, SMILES)
    assert good.chemically_eligible and good.physical_valid
    xyz = coordinates(mol)
    xyz[4] += [0, 0, 5]
    bad = tmp_path / "distorted.sdf"
    bad.write_text(sdf_text(set_coordinates(mol, xyz)))
    report = diagnostics(p, bad, SMILES)
    assert not report.physical_valid
    assert any(c.status == "fail" and c.category == "geometry" for c in report.checks)


def test_inverted_chirality_fails_identity(tmp_path):
    from boltzfix.chemistry import synthetic_fixture

    smiles = "C[C@H](O)c1ccccc1CCC"
    p, s, _, _ = synthetic_fixture(smiles, 1)
    pp = tmp_path / "p.pdb"
    ll = tmp_path / "l.sdf"
    pp.write_text(p)
    ll.write_text(s)
    mol = ligand_from_sdf(ll)
    xyz = coordinates(mol)
    xyz[:, 0] *= -1
    ll.write_text(sdf_text(set_coordinates(mol, xyz)))
    assert not diagnostics(pp, ll, smiles).chemically_eligible
