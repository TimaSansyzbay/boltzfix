# Cohort and offline evaluation

## Current state

The pipeline and engineering fixtures are implemented. No twenty-complex scientific cohort has been downloaded or admitted, no hosted Boltz predictions have been generated, and no superiority result exists. Access and the live schemas must be checked before a scientific campaign.

## Candidate inventory

Use [Runs N’ Poses](https://github.com/plinder-org/runs-n-poses) as a candidate source. Supply a JSON array of metadata records, keeping native files outside application storage. Paths resolve relative to the inventory. Example schema (values must describe actual inspected data):

```json
{
  "case_id": "source_identifier",
  "sequence": "100_TO_400_STANDARD_RESIDUES",
  "ligand_smiles": "FULLY_SPECIFIED_ISOMERIC_SMILES",
  "release_date": "2024-01-01",
  "method": "X-RAY DIFFRACTION",
  "resolution": 2.0,
  "complete_ligand": true,
  "complete_binding_site": true,
  "single_ligand_pose": true,
  "noncovalent": true,
  "no_essential_cofactor": true,
  "single_chain": true,
  "reference_protein": "native/protein.pdb",
  "reference_ligand": "native/ligand.sdf",
  "training_similarity_stratum": "source_defined_stratum",
  "msa": ">query\nACTUAL_QUERY_SEQUENCE\n..."
}
```

Eligibility metadata requires curator verification. The code validates sequence/ligand identity, file hashes, structural date, method/resolution and required flags; it does not independently prove experimental metadata or binding-site completeness. Do not set flags merely to pass admission.

Target six development and twenty evaluation complexes, released after 2023-06-01, X-ray resolution ≤2.5 Å, complete ligand and relevant binding-site coordinates, unambiguous pose and supported chemistry. Protein pairs with ≥50% identity at ≥80% coverage of the longer sequence, or ligand Morgan Tanimoto ≥0.8, join connected groups kept within one partition. Fixed seed: 20261031. Group assignment is deterministic and independent of prediction outcomes; inspect the reported training-similarity stratum counts before freezing. If those counts are unsuitable, curate a suitable inventory before evaluation, rather than editing the split after observing outcomes.

Curation writes `reference-manifest.json` and an anonymized `public-manifest.json`. The agent-facing campaign consumes only the latter. If exact eligible group counts cannot be supplied, the tool reports insufficiency instead of relaxing criteria. Public benchmarks and temporal/similarity filtering cannot prove independence from pretrained model exposure.

## Development and registration

Verify prediction conversion and CPU refinement on development cases. Compare both predefined hybrid schedules using development data only. Freeze the better schedule, resolving ties by lower average latency. Record that decision with the dated registration; evaluation code provides the schedule setting but does not automatically choose it from native evaluation structures.

Freeze model identifier, provider, request parameters, MSA, chemistry/physics protocol, selector, package versions and public manifest before the main campaign. `register` hashes these inputs; `benchmark` refuses tampered registrations or changed protocol constants. Avoid editing prompts, tools or packages during the campaign. Retain dated source bundles.

## Reference scoring

1. The investigation ends and freezes candidate ID, configuration hash and artifact hashes.
2. The evaluator verifies the receipt and files. It is not an API endpoint or agent tool.
3. Match protein residues by sequence, align matched Cα atoms using fixed Kabsch fitting, and apply that transform to the predicted ligand.
4. Calculate symmetry-corrected ligand heavy-atom RMSD **in place**, without ligand fitting.
5. Primary success: RMSD <2 Å and frozen applicable validity checks against the predicted receptor pass. Accuracy-only and validity-only are separate outcomes. Native-receptor validity is not currently calculated.

Failed cases remain in the complete campaign. `evaluate-campaign` checks every registered case/policy pair and writes explicit failures where the shared initial prediction failed. It refuses an incomplete campaign rather than dropping rows. `analyze` requires matched case sets, rejects duplicate repeats and refuses to mix synthetic and live results. Aggregate independent repeats by complex before bootstrap analysis.

The report includes selected-pose outcome, search success, resource usage and paired case-level differences with 10000 bootstrap resamples. Twenty independent complexes constitute a pilot. An interval spanning zero does not support a superiority claim. Synthetic reports always identify themselves as engineering exercises.

## Sources used in implementation

- [ScienceClaw judging](https://scienceclawhack.ai/#competition)
- [NVIDIA BioNeMo toolkit Boltz API reference](https://raw.githubusercontent.com/NVIDIA-BioNeMo/bionemo-agent-toolkit/main/nim-skills/boltz2-nim/references/api.md)
- [Nebius MCP setup](https://forge.nebius.cloud/mcp/)
- [PoseBusters](https://github.com/maabuu/posebusters)
- [OpenMM force-field infrastructure](https://github.com/openmm/openmmforcefields)
- [RDKit alignment and in-place RMSD API](https://www.rdkit.org/docs/source/rdkit.Chem.rdMolAlign.html)
- Installed Eve 1.0.0-rc.1 and AI SDK 7 documentation/source, used to verify the authored runtime APIs.
