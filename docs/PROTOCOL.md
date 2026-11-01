# Protocol v1

## Inputs and immutable settings

One standard protein chain, 100–400 residues; one fully specified, noncovalent organic ligand, 10–50 heavy atoms, formal charge −1 to +1. Unsupported metals, cofactors, multiple ligand fragments and ambiguous stereochemistry are rejected. The submitted ligand identity and charge are preserved; there is no automatic ligand protonation/tautomer search. Protein hydrogen preparation assumes pH 7.

Hosted requests use 3 recycles, 50 diffusion steps, 1 initial sample, no affinity calculation, no native templates/pockets/contacts, a fixed guidance setting and bounded additional batches of 1–3 samples. Main comparisons require a single cached A3M per case. The first alignment sequence must match the input protein. Source confidence is the NIM aggregate `confidence_scores` field, consistently mapped by candidate index. Deployment version remains `not_reported` when unavailable.

## Prediction-only diagnostics

Gemmi parses mmCIF. RDKit reconstructs bonding through the submitted graph, transferring chemistry by a verified graph match rather than assuming atom order. The ligand SDF stays in the predicted protein frame. Coordinate-derived stereochemistry is checked against the input. Atom mapping, raw output and normalized diagnostics are retained.

PoseBusters uses **dock** mode with the predicted protein and ligand only. Graph/charge/chirality, finite coordinates, distance geometry, planarity, intraligand clashes, protein contacts and available confidence are recorded. Every check distinguishes pass/fail/unknown/not-applicable/tool-error. A tool error cannot establish physical validity. The same checklist is used across policies. Source confidence is explicitly inherited for minimized children.

The artificial Cα scaffold in the offline demo is not a complete protein. It cannot be parameterized as one; a requested refinement returns a structured unavailable result. Mock mode never fabricates OpenMM energy or successful mechanics.

## Restrained minimization

The real action runs in a subprocess with a 300-second limit covering preparation and mechanics. It uses Amber14 ff14SB, explicitly selected OpenFF Sage `openff-2.2.1.offxml`, AmberTools AM1-BCC, protein hydrogens at pH 7, explicit TIP3P water with 1 nm padding, 0.15 M ions, PME, 1 nm cutoff and hydrogen-bond constraints. Missing sequence segments and nonstandard residues are rejected. Missing heavy atoms within existing standard residues may be added and are recorded.

Protein heavy-atom restraints: 1000 kJ mol⁻¹ nm⁻². Ligand heavy-atom restraints: 10 kJ mol⁻¹ nm⁻². Energy expression is `0.5*k*displacement²`. At most 200 minimizer iterations, tolerance 10 kJ mol⁻¹ nm⁻¹, OpenMM CPU platform with two threads. No MD trajectory, velocity initialization or GPU selection occurs.

Initial/final energy comes from the same constructed system. A preparation ZIP preserves the serialized OpenMM system, exact initial positions in nanometers, initial solvated PDB and protocol, allowing replay without re-running charge assignment or solvation. Refined complexes are exported as mmCIF with explicit ligand bonds. Protein Cα RMSD >0.5 Å or ligand RMSD >2 Å after parent-protein alignment marks a child outside the local protocol. Such children are retained but ineligible for final selection. Parent candidates are never overwritten; a prediction may have at most one refinement attempt, and refined children cannot be minimized again.

The CPU engineering test uses OpenMM's bundled villin example and generated aspirin. It validates the mechanics code, not a biological binding hypothesis.

## Coordination

Chemistry, structure and physics specialists have separate responsibilities and proposal evidence views. They see published proposals during review, can challenge them, and have no unrestricted tools. At most one proposal and one vote per specialist per round. Two endorsements execute an action. Ties rotate proposer priority by epoch. One revision round is allowed. Persistent disagreement stops the investigation.

The gateway validates evidence IDs, eligibility and remaining allowances; it does not rank scientific hypotheses with a central model. Each live decision invokes one Eve tool, one direct sponsor-backed model request, and one consumed SQLite reservation. Default Eve chat/home routes and default tools are disabled. SDK retries, model changes and paid fallbacks are disabled. JSON/schema failures are retained as failed decisions rather than allowing an uncontrolled repair loop.

The isolated policy prevents proposal/review exchange before deterministic plurality pooling. It still has the common observed candidate history, making it an isolation-of-communication ablation rather than fully independent laboratories.

## Selection and baselines

All policies use the same deterministic selector: reject incomplete/nonfinite/chemically inconsistent/stereochemically invalid and outside-protocol candidates; prefer all physical checks passing, then fewer failed checks, then fewer severe clashes, then source confidence, then stable candidate IDs. If no candidate passes physical checks, the best chemically intact candidate is flagged invalid. No eligible chemistry means no selection.

Baseline schedules:

- Initial only: contextual start, not an equal-spend comparator.
- Fixed resampling: use remaining sample allowance.
- Fixed hybrid: sample then refine, or the predefined alternate schedule. Choose and freeze the schedule using development data only.
- Rules: refine an unattempted original prediction with physical failures; otherwise sample while affordable.
- Single agent: one model with the combined evidence and identical service/LLM limits.
- Collective: specialist proposal/review protocol.

A common initial candidate is reused by campaign policies and **charged to every logical ledger**. This reduces physical experimental expense without making initial computation free in the comparison. Hosted stochastic samples may not be seed-controllable. Policy order is randomized per case after the shared initial prediction.

## Budgets and failure recovery

Demo: 2 prediction samples/requests, 1 refinement, 3 epochs, 18 model calls, 30000 aggregate tokens, 900 seconds. Benchmark: 4 samples/requests, 2 refinements, 6 epochs, 36 calls, 60000 tokens, 1800 seconds. Campaign: at most 500 dispatched sample units and 1500 model calls; verified sponsor allowance capped at USD 50. Reservations are atomic, including sponsor cost upper bounds. Archived replay shows historical resource use without new charges.

Unknown completion remains charged and blocks automatic dispatch. Authentication/quota errors pause the campaign. Cancellation stops dispatch, kills local mechanics and retains completed candidates. Synchronous remote work may remain uncancellable. A crashed worker pauses stale runs; no request with unknown completion is resubmitted. SQLite is authoritative; sequenced events support SSE reconnect and export.

## Claim boundaries

Fewer clashes establish improved geometric plausibility; greater source confidence establishes a model signal; lower same-system minimized energy establishes a force-field observation. None establishes native accuracy, affinity or efficacy. Accuracy needs isolated reference scoring. Report collective underperformance and uncertainty. Hosted sample allowances and observed wall time do not establish equal GPU work.
