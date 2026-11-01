from __future__ import annotations
import io
import math
from pathlib import Path
import gemmi
import numpy as np
from rdkit import Chem
from rdkit.Chem import AllChem, rdDetermineBonds
from Bio.Align import PairwiseAligner
from .errors import BoltzFixError
from .models import digest

STANDARD = set(
    "ALA ARG ASN ASP CYS GLN GLU GLY HIS ILE LEU LYS MET PHE PRO SER THR TRP TYR VAL".split()
)


def input_molecule(smiles: str, enforce_scope=True):
    mol = Chem.MolFromSmiles(smiles)
    if mol is None or len(Chem.GetMolFrags(mol)) != 1:
        raise BoltzFixError(
            "INPUT_INVALID", "Supply one connected, valid ligand SMILES"
        )
    if enforce_scope:
        if not 10 <= mol.GetNumHeavyAtoms() <= 50:
            raise BoltzFixError(
                "INPUT_INVALID", "v1 supports ligands with 10–50 heavy atoms"
            )
        if not -1 <= Chem.GetFormalCharge(mol) <= 1:
            raise BoltzFixError(
                "INPUT_INVALID", "v1 supports ligand formal charge −1, 0, or +1"
            )
        if any(
            a.GetSymbol() not in {"C", "N", "O", "S", "P", "F", "Cl", "Br", "I", "H"}
            for a in mol.GetAtoms()
        ):
            raise BoltzFixError(
                "INPUT_INVALID", "Metal-containing or unsupported ligand chemistry"
            )
        if any(
            str(s.specified) == "Unspecified" for s in Chem.FindPotentialStereo(mol)
        ):
            raise BoltzFixError(
                "INPUT_INVALID", "Specify all relevant ligand stereochemistry"
            )
    return mol


def ligand_from_sdf(path: str | Path):
    supplier = Chem.SDMolSupplier(str(path), removeHs=False, sanitize=True)
    mol = next(iter(supplier), None)
    if mol is None or mol.GetNumConformers() == 0:
        raise BoltzFixError(
            "MAPPING_ERROR", "Ligand SDF could not be loaded with coordinates"
        )
    return Chem.RemoveHs(mol)


def sdf_text(mol):
    return Chem.MolToMolBlock(mol) + "\n$$$$\n"


def combine_complex(protein_pdb, ligand_sdf, mapping):
    """Export refined heavy-atom coordinates and explicit ligand bonds as mmCIF."""
    mol = Chem.MolFromMolBlock(
        ligand_sdf.split("$$$$")[0], sanitize=True, removeHs=True
    )
    if mol is None:
        raise BoltzFixError(
            "MAPPING_ERROR", "Refined ligand could not be reconstructed"
        )
    structure = gemmi.read_pdb_string(protein_pdb)
    chain = gemmi.Chain("L" if all(c.name != "L" for c in structure[0]) else "Z")
    residue = gemmi.Residue()
    residue.name = "LIG"
    residue.seqid = gemmi.SeqId(1, " ")
    residue.het_flag = "H"
    names = [a["cif_atom_name"] for a in mapping["atoms"]]
    if len(names) != mol.GetNumAtoms():
        raise BoltzFixError("MAPPING_ERROR", "Refined ligand mapping length differs")
    for i, atom in enumerate(mol.GetAtoms()):
        record = gemmi.Atom()
        record.name = names[i]
        record.element = gemmi.Element(atom.GetSymbol())
        record.pos = gemmi.Position(*coordinates(mol)[i])
        record.occ = 1
        residue.add_atom(record)
    chain.add_residue(residue)
    structure[0].add_chain(chain)
    document = structure.make_mmcif_document()
    loop = document.sole_block().init_loop(
        "_chem_comp_bond.", ["comp_id", "atom_id_1", "atom_id_2", "value_order"]
    )
    orders = {
        Chem.BondType.SINGLE: "SING",
        Chem.BondType.DOUBLE: "DOUB",
        Chem.BondType.TRIPLE: "TRIP",
        Chem.BondType.AROMATIC: "AROM",
    }
    for bond in mol.GetBonds():
        loop.add_row(
            [
                "LIG",
                names[bond.GetBeginAtomIdx()],
                names[bond.GetEndAtomIdx()],
                orders[bond.GetBondType()],
            ]
        )
    return document.as_string()


def graph_key(mol):
    return Chem.MolToSmiles(Chem.RemoveHs(mol), isomericSmiles=True)


def coordinates(mol):
    xyz = np.asarray(mol.GetConformer().GetPositions(), dtype=float)
    if not np.isfinite(xyz).all():
        raise BoltzFixError("MAPPING_ERROR", "Nonfinite ligand coordinates")
    return xyz


def set_coordinates(mol, xyz):
    copy = Chem.Mol(mol)
    for i, p in enumerate(np.asarray(xyz)):
        copy.GetConformer().SetAtomPosition(i, tuple(float(x) for x in p))
    return copy


def extract_complex(cif_text: str, smiles: str):
    """Reconstruct chemistry from CIF bonds or coordinate connectivity + input template.

    No experimental structure or atom-index assumption is used. Ambiguous/invalid
    reconstruction is rejected rather than manufacturing a geometry score.
    """
    try:
        block = gemmi.cif.read_string(cif_text).sole_block()
        structure = gemmi.make_structure_from_block(block)
        if len(structure) != 1:
            raise ValueError("Exactly one model required")
        protein = gemmi.Structure()
        protein.name = "prediction"
        model = gemmi.Model("1")
        residues = []
        for chain in structure[0]:
            pc = gemmi.Chain(chain.name[:1] or "A")
            for residue in chain:
                if residue.name in STANDARD:
                    pc.add_residue(residue.clone())
                elif not residue.is_water():
                    heavy = [a for a in residue if not a.element.is_hydrogen]
                    if len(heavy) > 1:
                        residues.append((chain.name, residue, heavy))
            if len(pc):
                model.add_chain(pc)
        if len(model) == 1 and not residues:
            raise BoltzFixError(
                "LIGAND_COORDINATES_MISSING",
                "Provider returned protein coordinates without a ligand. Confidence scores cannot substitute for missing coordinates.",
            )
        if len(model) != 1 or len(residues) != 1:
            raise ValueError(
                "v1 requires one protein chain and one ligand, without additional cofactors"
            )
        protein.add_model(model)
        chain_name, residue, atoms = residues[0]
        if any(a.altloc not in {"\x00", " ", "A"} for a in atoms):
            raise ValueError("Alternate ligand coordinates are unsupported")
        template = input_molecule(smiles)
        if len(atoms) != template.GetNumHeavyAtoms():
            raise BoltzFixError(
                "LIGAND_ATOMS_MISSING",
                f"Returned ligand has {len(atoms)} heavy atoms; the submitted molecule requires {template.GetNumHeavyAtoms()}. Incomplete poses cannot be scored or refined.",
            )
        raw = Chem.RWMol()
        for atom in atoms:
            raw.AddAtom(Chem.Atom(atom.element.atomic_number))
        conf = Chem.Conformer(len(atoms))
        for i, atom in enumerate(atoms):
            conf.SetAtomPosition(i, (atom.pos.x, atom.pos.y, atom.pos.z))
        raw.AddConformer(conf)
        names = {a.name: i for i, a in enumerate(atoms)}
        if len(names) != len(atoms):
            raise ValueError("Nonunique ligand atom names")
        comp = list(block.find_values("_chem_comp_bond.comp_id"))
        left = list(block.find_values("_chem_comp_bond.atom_id_1"))
        right = list(block.find_values("_chem_comp_bond.atom_id_2"))
        order = list(block.find_values("_chem_comp_bond.value_order"))
        bonds = 0
        types = {
            "SING": Chem.BondType.SINGLE,
            "DOUB": Chem.BondType.DOUBLE,
            "TRIP": Chem.BondType.TRIPLE,
            "AROM": Chem.BondType.AROMATIC,
        }
        for co, a, b, o in zip(comp, left, right, order):
            if co == residue.name and a in names and b in names:
                raw.AddBond(
                    names[a], names[b], types.get(o.upper(), Chem.BondType.SINGLE)
                )
                bonds += 1
        raw = raw.GetMol()
        if not bonds:
            rdDetermineBonds.DetermineConnectivity(raw)
        ligand = AllChem.AssignBondOrdersFromTemplate(template, raw)
        # Connectivity perception disables implicit H on bare coordinate atoms.
        # Transfer chemistry through a verified graph match, never raw atom order.
        matches = ligand.GetSubstructMatches(
            template, uniquify=False, useChirality=False, maxMatches=10000
        )
        if not matches:
            raise ValueError("No complete chemical graph correspondence")
        match = matches[0]
        for template_index, coordinate_index in enumerate(match):
            source = template.GetAtomWithIdx(template_index)
            target = ligand.GetAtomWithIdx(coordinate_index)
            target.SetFormalCharge(source.GetFormalCharge())
            target.SetNumExplicitHs(source.GetNumExplicitHs())
            target.SetNoImplicit(source.GetNoImplicit())
            target.SetNumRadicalElectrons(source.GetNumRadicalElectrons())
        ligand.UpdatePropertyCache(strict=True)
        Chem.SanitizeMol(ligand)
        Chem.AssignStereochemistryFrom3D(ligand, replaceExistingTags=True)
        if graph_key(ligand) != graph_key(template):
            raise ValueError(
                "Reconstructed 3D chemistry/stereochemistry disagrees with input"
            )
        mapping = {
            "ligand_chain": chain_name,
            "residue": residue.name,
            "graph": graph_key(ligand),
            "atoms": [
                {"index": i, "cif_atom_name": a.name, "element": a.element.name}
                for i, a in enumerate(atoms)
            ],
            "protein_atoms": [
                {
                    "chain": ch.name,
                    "residue": r.seqid.num,
                    "insertion_code": r.seqid.icode,
                    "resname": r.name,
                    "atom": a.name,
                    "element": a.element.name,
                }
                for ch in structure[0]
                for r in ch
                if r.name in STANDARD
                for a in r
                if not a.element.is_hydrogen
            ],
            "connectivity_source": (
                "cif_chem_comp_bond"
                if bonds
                else "coordinate_connectivity_and_input_template"
            ),
        }
        return protein.make_pdb_string(), sdf_text(ligand), mapping
    except BoltzFixError:
        raise
    except Exception as exc:
        raise BoltzFixError(
            "MAPPING_ERROR", f"Complex reconstruction failed: {exc}"
        ) from exc


def protein_atoms(path):
    s = gemmi.read_structure(str(path))
    atoms = []
    for chain in s[0]:
        for res in chain:
            if res.name not in STANDARD:
                continue
            for a in res:
                if not a.element.is_hydrogen:
                    atoms.append(
                        {
                            "chain": chain.name,
                            "residue": res.seqid.num,
                            "resname": res.name,
                            "atom": a.name,
                            "element": a.element.atomic_number,
                            "xyz": [a.pos.x, a.pos.y, a.pos.z],
                        }
                    )
    if not atoms:
        raise BoltzFixError("MAPPING_ERROR", "No supported protein atoms")
    return atoms


def ca_sequence(path):
    s = gemmi.read_structure(str(path))
    seq = []
    xyz = []
    for chain in s[0]:
        for res in chain:
            if res.name not in STANDARD:
                continue
            ca = res.find_atom("CA", "*")
            if ca:
                seq.append(gemmi.find_tabulated_residue(res.name).one_letter_code)
                xyz.append([ca.pos.x, ca.pos.y, ca.pos.z])
    if len(xyz) < 3:
        raise BoltzFixError(
            "ALIGNMENT_ERROR", "At least three protein Cα atoms are required"
        )
    return "".join(seq), np.array(xyz)


def kabsch(mobile, target):
    x, y = np.asarray(mobile), np.asarray(target)
    u, _, vt = np.linalg.svd((x - x.mean(0)).T @ (y - y.mean(0)))
    if np.linalg.det(u @ vt) < 0:
        u[:, -1] *= -1
    rot = u @ vt
    trans = y.mean(0) - x.mean(0) @ rot
    rms = float(np.sqrt(np.mean(np.sum((x @ rot + trans - y) ** 2, axis=1))))
    return rot, trans, rms


def protein_transform(mobile_path, target_path):
    ms, m = ca_sequence(mobile_path)
    ts, t = ca_sequence(target_path)
    aligner = PairwiseAligner()
    aligner.match_score = 2
    aligner.mismatch_score = -1
    aligner.open_gap_score = -2
    aligner.extend_gap_score = -0.5
    a = aligner.align(ms, ts)[0]
    mi = []
    ti = []
    for (start, end), (start2, end2) in zip(*a.aligned):
        mi.extend(range(int(start), int(end)))
        ti.extend(range(int(start2), int(end2)))
    if len(mi) < 3 or len(mi) / max(len(m), len(t)) < 0.8:
        raise BoltzFixError(
            "ALIGNMENT_ERROR", "Insufficient sequence-matched protein coverage"
        )
    rot, trans, rms = kabsch(m[mi], t[ti])
    return rot, trans, rms, len(mi)


def in_place_rmsd(mobile, reference):
    if graph_key(mobile) != graph_key(reference):
        raise BoltzFixError("MAPPING_ERROR", "Ligand identities differ")
    # CalcRMS enumerates equivalent atom orders but DOES NOT fit ligand coordinates.
    return float(
        AllChem.CalcRMS(
            Chem.RemoveHs(mobile), Chem.RemoveHs(reference), maxMatches=100000
        )
    )


def compare_poses(protein_path, ligand_path, reference_protein, reference_ligand):
    rot, trans, protein_rms, matched = protein_transform(
        protein_path, reference_protein
    )
    ligand = ligand_from_sdf(ligand_path)
    aligned = set_coordinates(ligand, coordinates(ligand) @ rot + trans)
    return {
        "ligand_rmsd": in_place_rmsd(aligned, ligand_from_sdf(reference_ligand)),
        "protein_ca_rmsd": protein_rms,
        "matched_ca": matched,
        "rotation": rot.tolist(),
        "translation": trans.tolist(),
    }


def synthetic_fixture(smiles: str, sample_index=0, seed=20261031):
    """Artificial Cα scaffold and generated ligand; NEVER experimental/model output."""
    mol = Chem.AddHs(input_molecule(smiles))
    params = AllChem.ETKDGv3()
    params.randomSeed = seed % 2147483647
    if AllChem.EmbedMolecule(mol, params) != 0:
        raise BoltzFixError(
            "MOCK_FIXTURE_ERROR", "Could not generate synthetic conformer"
        )
    AllChem.MMFFOptimizeMolecule(mol, maxIters=100)
    mol = Chem.RemoveHs(mol)
    xyz = coordinates(mol)
    xyz -= xyz.mean(0)
    center = (
        np.array([6.0, 0.0, 0.0])
        if sample_index == 0
        else np.array([0.0, 0.0, 6.0 + 0.2 * (sample_index % 3)])
    )
    mol = set_coordinates(mol, xyz + center)
    lines = []
    for i in range(100):
        angle = i * math.radians(100)
        x, y, z = 6 * math.cos(angle), 6 * math.sin(angle), i * 1.5
        lines.append(
            f"ATOM  {i+1:5d}  CA  ALA A{i+1:4d}    {x:8.3f}{y:8.3f}{z:8.3f}  1.00 80.00           C  "
        )
    protein = "\n".join(lines) + "\nTER\nEND\n"
    structure = gemmi.read_pdb_string(protein)
    lc = gemmi.Chain("L")
    residue = gemmi.Residue()
    residue.name = "LIG"
    residue.seqid = gemmi.SeqId(1, " ")
    residue.het_flag = "H"
    mapping = []
    for i, a in enumerate(mol.GetAtoms()):
        ga = gemmi.Atom()
        ga.name = f"{a.GetSymbol()}{i+1}"
        ga.element = gemmi.Element(a.GetSymbol())
        ga.pos = gemmi.Position(*coordinates(mol)[i])
        ga.occ = 1
        residue.add_atom(ga)
        mapping.append({"index": i, "cif_atom_name": ga.name, "element": a.GetSymbol()})
    lc.add_residue(residue)
    structure[0].add_chain(lc)
    return (
        protein,
        sdf_text(mol),
        structure.make_mmcif_document().as_string(),
        {"atoms": mapping, "graph": graph_key(mol), "synthetic": True},
    )
