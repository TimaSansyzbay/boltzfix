# BoltzFix

A local research application for bounded protein–ligand pose investigations. Three specialists propose actions, review evidence and require two endorsements to run an experiment. A deterministic gateway enforces limits; a deterministic selector freezes the final candidate. Experimental coordinates are used only by a separate offline evaluator.

**No API credentials are included. Live execution is disabled by default.** The synthetic example runs without external services. A tested archived example is included at `fixtures/archived-synthetic.zip`. It exercises actual chemistry diagnostics and application logic, with artificial structures and authored mock decisions. It is not evidence of a scientific improvement.

## Product workflows (v2)

Click **New investigation**. The default **Review existing predictions** accepts a protein PDB and one to four ligand SDF poses in that protein's coordinate frame, with the matching isomeric SMILES. **Load real example** loads a genuine previously generated prediction, not an experimental reference. Choose **Checks only** for immediate local diagnostics without an LLM, **Automated checks and refinement** for rules plus CPU mechanics, or the **Three-agent collective** for live specialist decisions. Imported runs reserve zero prediction samples and cannot dispatch remote resampling.

**Generate poses · NVIDIA DiffDock** accepts a supplied predicted protein PDB and SMILES, creates two ligand poses in one explicitly selected hosted request, verifies complete atom identity and measures pose diversity. This is a different prediction method and protocol from Boltz cofolding, not an automatic fallback. Its confidence is an uncalibrated ranking score. Sponsor-backed access additionally requires `BOLTZFIX_DIFFDOCK_FREE_TRIAL_CONFIRMED=1`; the bundled launcher reads the private runtime configuration. The original sequence/Boltz path is marked experimental because its current hosted exporter can omit ligand coordinates.

After completion, open **Results & artifacts → Research report** for a self-contained HTML report, **Report Markdown** for editable text, or **Run bundle** for all structures and provenance. Reports require no additional LLM calls. Accuracy remains unverified until an isolated matching experimental evaluation is imported.

New endpoints: `POST /v1/runs/import` (multipart protein, ligand files, SMILES, settings), `POST /v1/structures/protein`, `GET /v1/examples/{retinol|brd4}`, and `GET /v1/runs/{id}/report?format=html|md`.

## Start on this workspace

From the parent workspace:

```sh
python3 boltzfix/scripts/dev.py
```

Open **http://127.0.0.1:5173** and click **Run synthetic example**. The launcher selects the installed Python 3.11 physics environment and Node 24 runtime. Ctrl+C stops all three services. The API is at `127.0.0.1:8000`; the private Eve service is at `127.0.0.1:8787`. If either preferred runtime has moved, set `BOLTZFIX_PYTHON` and `BOLTZFIX_NODE` to executable paths.

The interface has structure overlays, clash markers, candidate genealogy, specialist proposals/reviews, resource accounting and ZIP exports. Results can import a local offline evaluation JSON **after selection freeze**, checking the receipt hash. Reference data is not sent to the backend or agents by that import.

## Install on another machine

Use Node.js 24 and Python 3.11. For a full CPU mechanics environment:

```sh
cd boltzfix
conda env create -f science/environment.yml
conda activate boltzfix-science
python -m pip install -r science/requirements-py311.lock
python -m pip install --no-deps -e science
npm ci
```

Set `BOLTZFIX_PYTHON` to that environment's Python executable, then run `python scripts/dev.py`. Run scientific commands from `boltzfix/`, or set `PYTHONPATH` to its `science/` directory. The outer workspace folder has the same name as the Python package, so running an editable installation from its parent without that path can shadow the package.

`conda-osx-arm64.lock` captures the tested conda packages on Apple Silicon. `environment.yml` is the portable specification; the pip lock captures the Python 3.11 application dependencies. Linux container definitions are supplied for science and agents but were **not built here**: a reachable Docker build environment was not verified in this session. They bind loopback and are intended for a private shared-host-network setup, with the same durable `/data` directory for both services. Cross-platform dependency resolution still needs verification.

For a lighter application-only environment, Python 3.12 also passed the non-physics tests. Install `science[test]`; missing OpenFF/AmberTools are reported explicitly. A separate full physics environment can be selected through `BOLTZFIX_PHYSICS_PYTHON`.

## What is implemented

| Area | Implementation |
|---|---|
| Scientific contracts | Strict Pydantic inputs, immutable configurations, candidate genealogy, diagnostic statuses, selection receipts. |
| Chemistry | RDKit/Gemmi conversion, graph-based bonding/mapping, coordinate chirality, finite coordinates, severe contacts and PoseBusters dock checks. No native ligand is passed to diagnostics. |
| Mechanics | Real subprocess-isolated OpenMM CPU minimization with Amber14, explicit Sage 2.2.1, AmberTools AM1-BCC, TIP3P/PME, fixed restraints and displacement checks. |
| Coordination | Independent specialist proposals, peer reviews, two-vote execution, one revision round, rotating priority and deterministic stopping on disagreement. |
| LLM runtime | Eve custom channel + bounded AI SDK decision tool. The normal Eve chat/home routes and default tools are disabled. One-time SQLite reservations prevent duplicate/unmetered decision calls. No SDK retries or automatic model fallback. |
| Comparators | Initial only, fixed resampling, fixed hybrid, rules, strong single agent, collective, optional isolated pooling. |
| Resources | Atomic SQLite reservations, conservative failed/unknown charging, per-run limits, campaign sample/call/cost caps, wall time, cancellation and stale-worker pause. |
| Prediction | Offline synthetic provider; documented NVIDIA hosted Boltz-2 adapter; authenticated Nebius MCP schema discovery. Nebius dispatch remains disabled until its live tool mapping is verified. |
| Evaluation | Frozen-selection isolation, sequence-based protein alignment, symmetry-corrected in-place ligand RMSD, physical validity endpoint, all-case campaign scoring, paired bootstrap and CSV reporting. |
| Application | React/TypeScript/Vite + FastAPI, typed OpenAPI client, SSE reconnect, downloads, archived replay, private local origin checks. |

The specialists use different evidence views for independent proposals. Reviews see the shared board. They have no filesystem, shell, browser or reference-retrieval tools. The Python application owns coordination and persistence; Eve owns the bounded model decision tool. It does not run a separate central scientific planning LLM.

## Commands

All commands below run from `boltzfix/` with the environment activated.

```sh
# No external calls or credentials
boltzfix preflight
boltzfix demo --data .boltzfix

# An immutable run JSON; no credential fields are accepted
boltzfix run fixtures/demo.json --data .boltzfix

# Reusable diagnostics, independent of LLMs and references
boltzfix diagnostics protein.pdb ligand.sdf 'ISOMERIC_SMILES' diagnostics.json

# Save a complete archived bundle, then import it without execution
boltzfix export RUN_ID archived.zip --data .boltzfix
boltzfix replay archived.zip --data .boltzfix

# Curate supplied candidate metadata before looking at predictions
boltzfix curate inventory.json reference-vault/cohort

# Freeze a public, reference-free manifest and experiment configuration
boltzfix register reference-vault/cohort/public-manifest.json campaign/registered.json --provider mock

# Stop the API worker before running a CLI campaign against the same store
boltzfix benchmark campaign/registered.json campaign/execution --data .boltzfix

# Offline: reference coordinates never enter the investigation API
boltzfix evaluate RUN_ID native-protein.pdb native-ligand.sdf reference-vault/scores --data .boltzfix
boltzfix evaluate-campaign campaign/registered.json campaign/execution/campaign.jsonl reference-vault/cohort/reference-manifest.json reference-vault/scores --data .boltzfix
boltzfix analyze reference-vault/scores campaign/report
```

Use `boltzfix --help` and subcommand `--help` for argument details. `fixtures/synthetic-manifest.json` supports an engineering campaign without a real cohort. It must not be presented as scientific validation.

## Add sponsor access later

There are **no `.env` files, embedded keys, or login steps** in the source. The launcher can load an optional owner-only credential JSON outside the application, then passes each credential only to its relevant backend service. The frontend receives none of the application's credential variables. Do not put credentials in run JSON, frontend code, exports or source control.

To enter or rotate local credentials without putting them in shell history:

```sh
python scripts/local_credentials.py
```

The prompts hide input. The default file is `../work/private/boltzfix-credentials.json`, with directory permissions 0700 and file permissions 0600. `BOLTZFIX_CREDENTIALS_FILE` can select another owner-only file. Existing server environment variables take precedence. Restart the launcher to load changes. Saving credentials does **not** enable inference or confirm sponsorship; the live flags and cost bounds below are still required. Source releases traverse only the application directory, so the default credential location is excluded.

| Variable | Purpose |
|---|---|
| `BOLTZFIX_ALLOW_LIVE` | Must explicitly be `1` for external inference. |
| `BOLTZFIX_SPONSOR_CONFIRMED` | Must explicitly be `1`; confirms sponsor-backed access. |
| `NVIDIA_API_KEY` or `NGC_API_KEY` | Later-provided NVIDIA credential. |
| `ANTHROPIC_API_KEY` | Later-provided sponsor Anthropic credential. |
| `BOLTZFIX_SAMPLE_USD_BOUND` | Verified maximum sponsor-credit charge per prediction sample; zero is accepted only for the explicitly confirmed NVIDIA free development API. |
| `BOLTZFIX_NVIDIA_FREE_TRIAL_CONFIRMED` | Set to `1` only after verifying NVIDIA's free development endpoint; sample and request limits still apply. |
| `BOLTZFIX_LLM_CALL_USD_BOUND` | Verified positive maximum sponsor-credit charge per bounded LLM call. |
| `BOLTZFIX_SPONSOR_USD_CAP` | Verified available sponsor allowance; capped by the application at USD 50. |
| `BOLTZFIX_LLM_MODEL` | Defaults to `claude-opus-5-5`; must match the immutable run configuration and returned model ID. |
| `NEBIUS_SCIENTIFIC_AI_API_KEY`, `NEBIUS_MCP_URL` | Later authenticated MCP discovery. Endpoint/schema must be verified. |
| `BOLTZFIX_DATA` | Shared local SQLite/artifact directory, identical for Python and Eve. |
| `BOLTZFIX_PHYSICS_PYTHON` | Optional separate CPU physics executable. |

The cost bounds reserve sponsor allowance **before** dispatch and stay charged conservatively. They are not invented price estimates. Verify the bounds against the sponsor account before enabling access. No fallback creates a paid account, provisions hardware or changes providers/models.

The launcher also reads optional non-secret settings from `../work/private/boltzfix-runtime.json`. Sponsor access has been confirmed by the user. The temporary preflight cap was USD 5; after the successful live collective check, the original user-approved USD 50 campaign ceiling was restored. The application reserves USD 0.30 per bounded Opus call and meters NVIDIA's documented free development API by sample/request counts. The Opus reservation is a conservative limit, not the measured charge. Actual sponsor balance remains unverified.

Authenticated checks: Opus 5.5 returned a valid structured decision (1419 total tokens). Three NVIDIA SMILES requests returned incomplete ligands: lysozyme + N-acetylglucosamine omitted the ligand, and aspirin returned 9 of 13 heavy atoms. Outputs were rejected and retained. NVIDIA's public runtime has a matching regression test, `nvbug 6932678`, describing dropped SMILES atom names. Its hosted deployment version is not reported, so the precise deployed revision remains unknown. A subsequent, metered **CCD NAG** request on the identical protein and ligand returned a complete, chemically verified candidate. The former global preflight hold has been removed locally; SMILES dispatch remains disabled.

The adapter now resolves the submitted SMILES against RCSB's public chemical dictionary, then independently verifies exact graph, charge and stereochemistry with RDKit before using NVIDIA's supported `ccd` encoding. Original SMILES remain immutable. No protonation, tautomer or similar-molecule substitution is allowed. The resolver fetches chemical definitions only, never experimental complexes or native poses. Its cached descriptor, source, identity hash and timestamp enter candidate provenance and exports. Verified entries in `$BOLTZFIX_DATA/chemical_dictionary/` are reused across policies; freeze and archive these records before a benchmark. First-time matching sends the ligand SMILES to the public RCSB search API, with no NVIDIA or Anthropic credentials attached.

An unmatched ligand pauses with `CCD_MATCH_REQUIRED` **before** reserving a prediction sample. Dictionary outages also pause without dispatch. Such molecules need a provider with a corrected SMILES exporter; no paid or hardware fallback is automatic. A verified chemical identity does not establish a correct binding pose, and every returned structure still undergoes the original atom-completeness and stereochemistry checks. `BOLTZFIX_NVIDIA_PREFLIGHT_BLOCK=incomplete_ligand_coordinates` remains available as an explicit emergency dispatch hold.

New runs now default to `claude-opus-5-5` at the user's request. Existing stored configurations keep their recorded model. The decision service uses native JSON-schema output and low effort, with up to 4096 output tokens inside the existing 12000-token per-call reservation. Opus 5.5 always uses adaptive thinking; the integration does not try to disable it or force a tool call. See [Anthropic's Opus 5.5 documentation](https://platform.claude.com/docs/en/models/opus-5-5/overview).

Agent token budgeting now counts the SDK's final serialized Anthropic request, including system instructions and the native JSON schema, through the official `messages/count_tokens` endpoint. Its estimate gets a margin of 10% or 256 tokens, whichever is larger. Only the output limit is reduced to fit the original reservation; scientific evidence is never truncated. Token counting is a free preflight, not another generation. Actual returned usage is still checked against the reservation. Unavailable counting pauses without generation, and typed local errors now reach the UI instead of becoming generic `AGENT_SERVICE`. Proven undispatched failures consume an attempted-call unit but release unused token and cost reservations; unknown remote work remains charged.

Three specialists still propose independently. Reviews stop once two votes make the winner irreversible. Duplicate equivalent actions prefer the first compatible proposal to avoid splitting votes across identifiers; unsupported evidence may still be challenged. This coordination/prompt change must be frozen with the source before a benchmark. A new live integration run (`run_300951ef7953453c`) completed with one real CCD NAG prediction, three Opus proposals, two reviews and 10,851 actual LLM tokens in about 24 seconds. This used a restricted one-epoch profile with no further prediction or refinement allowance. It verifies agent service integration, not adaptive search superiority.

After access is supplied, run discovery/preflight, verify the deployed request/response schema, candidate-confidence ordering, MSA support, quota and deployment identity. Run one actual prediction on a development case. Nebius discovery does **not** imply its scientific payload is implemented; its adapter intentionally refuses dispatch until the discovered tools are mapped. The NVIDIA synchronous adapter has no verified polling/cancellation endpoint; interrupted requests remain unresolved and are never automatically resubmitted.

## Frozen scientific protocol

See [docs/PROTOCOL.md](docs/PROTOCOL.md) for preparation, minimization, selection, baselines and claim boundaries, and [docs/EVALUATION.md](docs/EVALUATION.md) for cohort metadata and analysis. Live campaigns require a cached MSA per case. Interactive runs may omit it but explicitly record service-default MSA handling; they are not suitable matched-MSA comparisons.

The primary outcome is **selected ligand heavy-atom RMSD <2 Å after protein alignment AND applicable physical checks passed**. The ligand is never independently aligned to its native pose. Better confidence, fewer clashes and lower energy are separate observations.

## Verification

```sh
python -m pytest science/tests -q
npm test
npm run build
npm run build:eve --workspace agents
# Start the stack first. Install Playwright Chromium or select an existing browser.
npx playwright test --config web/playwright.config.ts
```

Set `BOLTZFIX_PHYSICS_PYTHON` for the full mechanics integration test. Set `BOLTZFIX_TEST_BROWSER` to an existing Chrome/Chromium executable if the Playwright browser is not installed.

Verified locally: Python scientific/backend tests, actual CPU mechanics, Node reservation/decision tests, frontend tests, production frontend build, Eve compilation, browser-to-API-to-artifact flow and responsive layout. A real Opus 5.5 decision and one complete hosted CCD NAG prediction passed their respective integration checks. These separate checks do not establish collective superiority. Sponsor balances and a curated twenty-complex scientific comparison remain unverified. **The software is a research prototype, not a demonstrated accuracy improvement.**

Latest real-data desktop end-to-end check: a normal-form lysozyme–NAG collective completed in 34.6 seconds with six Opus decisions. A beta-lactoglobulin–retinol fixed-hybrid run completed two hosted predictions and genuine CPU minimization (114.4 seconds for minimization, 123.4 seconds overall), preserving its parent and exporting a valid child. Invalid input, zero-dispatch queued cancellation, browser downloads, artifact hashes, inherited confidence, offline-evaluation import and run switching were checked through the UI. A compact run picker and URL-based run selection now keep history accessible in narrow windows. Frozen-selection exports are separated from previewed-candidate exports, with a regression test preventing the two from being confused.

This check also recorded failures. BRD4–JQ1 CCD output still omitted every ligand coordinate; the CCD workaround therefore does **not** establish general ligand compatibility. The application rejected it without relaxing validation. Two separate retinol single-sample requests returned essentially identical poses. Independent experimental scoring against public 1GX8, performed only after selection freeze, gave **6.124 Å** ligand RMSD for the selected prediction and **6.288 Å** for the minimized child. Neither met the 2 Å accuracy target, despite passing physical checks. Treat these historical cases as integration controls, not held-out scientific evidence. Provider export coverage, sampling diversity and a proper matched benchmark remain necessary before claiming scientific improvement.

## ScienceClaw

The official [judging rubric](https://scienceclawhack.ai/#competition) emphasizes scientific results and collective capability. This implementation provides the experiment and auditable artifacts; synthetic operation earns no scientific-superiority claim. Demonstrate collective vs single/rules/fixed policies on the same frozen cohort and ceilings, report negative outcomes, and retain dated releases. Diagnostics are reusable through CLI and `POST /v1/diagnostics`; actual reuse by another team must be documented separately for a collaboration claim.
