# CPU mechanics engineering fixture

`protein.pdb` is the 34-residue villin structure in OpenMM's distributed `app/data/test.pdb`, stripped of solvent and hydrogens. OpenMM is MIT licensed (https://github.com/openmm/openmm). Preserve its license when redistributing this example.

`ligand.sdf` is an RDKit-generated aspirin conformer placed near the protein, without docking or an experimental binding hypothesis. The pair is **only an engineering test of parameterization, restraints, solvation and minimization**. It is not a measured protein–ligand complex, a Boltz output, or an accuracy benchmark. Its protein length is deliberately below the application eligibility limit; the mechanics function is tested independently of product input admission.

No native ligand structure is used. The test checks identity preservation, finite same-system energies and local coordinate displacement. Lower energy does not establish a correct pose.
