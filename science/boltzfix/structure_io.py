"""Validated structure input. No reference retrieval, pose fitting or atom invention."""

from __future__ import annotations

import math
import gemmi
from rdkit import Chem

from .chemistry import (
    STANDARD,
    combine_complex,
    extract_complex,
    input_molecule,
    sdf_text,
)
from .errors import BoltzFixError


def protein_input(text: str):
    try:
        structure = gemmi.read_pdb_string(text)
        if len(structure) != 1:
            raise ValueError("Supply exactly one PDB model")
        chains = []
        for chain in structure[0]:
            standard = [r for r in chain if r.name in STANDARD]
            if standard:
                chains.append((chain, standard))
            if any(r.name not in STANDARD and not r.is_water() for r in chain):
                raise ValueError(
                    "Supply protein only; additional ligands, ions or cofactors are outside this version's scope"
                )
        if len(chains) != 1:
            raise ValueError("Supply exactly one standard protein chain")
        chain, residues = chains[0]
        clean = gemmi.Structure()
        model = gemmi.Model("1")
        target = gemmi.Chain(chain.name[:1] or "A")
        sequence = ""
        for residue in residues:
            names = set()
            r = gemmi.Residue()
            r.name, r.seqid, r.het_flag = residue.name, residue.seqid, "A"
            for atom in residue:
                if atom.element.is_hydrogen:
                    continue
                if atom.altloc not in {"\x00", " "} or atom.name in names:
                    raise ValueError(
                        "Resolve alternate or duplicate atom positions before import"
                    )
                if not all(
                    math.isfinite(v) for v in [atom.pos.x, atom.pos.y, atom.pos.z]
                ):
                    raise ValueError("Protein coordinates must be finite")
                names.add(atom.name)
                r.add_atom(atom.clone())
            if "CA" not in names:
                raise ValueError("Every imported residue needs a C-alpha position")
            target.add_residue(r)
            sequence += gemmi.find_tabulated_residue(residue.name).one_letter_code
        if not 100 <= len(sequence) <= 400:
            raise ValueError(
                "This version supports one protein chain with 100–400 residues"
            )
        model.add_chain(target)
        clean.add_model(model)
        return clean.make_pdb_string(), sequence
    except BoltzFixError:
        raise
    except Exception as exc:
        raise BoltzFixError("PROTEIN_INPUT", str(exc)) from exc


def prepare_pair(protein_pdb: str, ligand_sdf: str, smiles: str):
    protein, _ = protein_input(protein_pdb)
    try:
        supplier = Chem.ForwardSDMolSupplier(
            __import__("io").BytesIO(ligand_sdf.encode()), removeHs=True
        )
        molecules = list(supplier)
        if len(molecules) != 1 or molecules[0] is None:
            raise ValueError("Supply one ligand conformer per SDF input")
        mol = molecules[0]
        if mol.GetNumConformers() != 1:
            raise ValueError("The ligand needs complete 3D coordinates")
        input_molecule(smiles)
        mapping = {
            "atoms": [
                {
                    "index": a.GetIdx(),
                    "cif_atom_name": f"{a.GetSymbol()}{a.GetIdx()+1}",
                    "element": a.GetSymbol(),
                }
                for a in mol.GetAtoms()
            ]
        }
        complex_cif = combine_complex(protein, sdf_text(mol), mapping)
        p, l, verified = extract_complex(complex_cif, smiles)
        return p, l, verified, complex_cif
    except BoltzFixError:
        raise
    except Exception as exc:
        raise BoltzFixError(
            "LIGAND_INPUT", f"Ligand structure could not be verified: {exc}"
        ) from exc


def conformer_from_smiles(smiles: str, seed: int):
    from rdkit.Chem import AllChem

    mol = Chem.AddHs(input_molecule(smiles))
    params = AllChem.ETKDGv3()
    params.randomSeed = seed % (2**31 - 1)
    if AllChem.EmbedMolecule(mol, params) != 0:
        raise BoltzFixError(
            "CONFORMER_UNAVAILABLE", "Could not generate a ligand input conformer"
        )
    return sdf_text(mol)
