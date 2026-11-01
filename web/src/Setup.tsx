import { useState } from "react";
import type { FormEvent } from "react";
import { ArrowUpRight, FlaskConical } from "lucide-react";
import { api, demoConfig } from "./api";
import type { RunConfig } from "./api";

type Workflow = "review" | "dock" | "sequence" | "demo";
type Example = {
  name: string;
  sequence: string;
  ligand_smiles: string;
  protein_pdb: string;
  ligand_sdf: string;
  provenance: string;
};

export function Setup({
  onCreated,
  onClose,
}: {
  onCreated: (id: string) => void;
  onClose: () => void;
}) {
  const [workflow, setWorkflow] = useState<Workflow>("review");
  const [name, setName] = useState("Protein–ligand investigation");
  const [smiles, setSmiles] = useState("");
  const [sequence, setSequence] = useState("");
  const [protein, setProtein] = useState("");
  const [poses, setPoses] = useState<string[]>([]);
  const [inputLabel, setInputLabel] = useState("");
  const [policy, setPolicy] =
    useState<NonNullable<RunConfig["policy"]>>("collective");
  const [extended, setExtended] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [exampleNote, setExampleNote] = useState("");
  async function example() {
    setError("");
    setBusy(true);
    try {
      const value = await api<Example>(
        `/v1/examples/${workflow === "dock" ? "brd4" : "retinol"}`,
      );
      setName(value.name);
      setSequence(value.sequence);
      setSmiles(value.ligand_smiles);
      setProtein(value.protein_pdb);
      setPoses([value.ligand_sdf]);
      setInputLabel("Real model-predicted example loaded");
      setExampleNote(value.provenance);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Example unavailable");
    } finally {
      setBusy(false);
    }
  }
  async function loadProtein(file: File | undefined) {
    if (!file) return;
    if (file.size > 3000000) {
      setError("Maximum 3 MB protein input");
      return;
    }
    setProtein(await file.text());
    setInputLabel(file.name);
    setExampleNote("");
  }
  async function loadPoses(files: FileList | null) {
    if (!files) return;
    if (files.length > 4 || Array.from(files).some((f) => f.size > 4000000)) {
      setError("Upload up to four SDF files, maximum 4 MB each");
      return;
    }
    setPoses(await Promise.all(Array.from(files).map((f) => f.text())));
    setExampleNote("");
  }
  async function submit(event: FormEvent) {
    event.preventDefault();
    setError("");
    setBusy(true);
    try {
      if (workflow === "demo") {
        const run = await api<{ id: string }>("/v1/runs", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(demoConfig),
        });
        onCreated(run.id);
        return;
      }
      const budget = {
        samples: workflow === "review" ? 0 : extended ? 4 : 2,
        prediction_requests: workflow === "review" ? 0 : extended ? 4 : 2,
        refinements: extended ? 2 : 1,
        epochs: extended ? 6 : 3,
        llm_calls: extended ? 36 : 18,
        llm_tokens: extended ? 60000 : 30000,
        wall_seconds: extended ? 1800 : 900,
        refinement_timeout: 300,
      };
      const settings = {
        name,
        case_id: "interactive",
        policy,
        agent_mode:
          policy === "collective" || policy === "single_agent"
            ? "live"
            : "synthetic",
        budget,
        llm_model: "claude-opus-5-5",
        protocol_version: "boltzfix-v2",
      };
      if (workflow === "review") {
        const form = new FormData();
        form.append(
          "protein",
          new Blob([protein], { type: "chemical/x-pdb" }),
          "protein.pdb",
        );
        poses.forEach((pose, i) =>
          form.append(
            "ligands",
            new Blob([pose], { type: "chemical/x-mdl-sdfile" }),
            `pose-${i + 1}.sdf`,
          ),
        );
        form.append("smiles", smiles);
        form.append("settings", JSON.stringify(settings));
        const run = await api<{ id: string }>("/v1/runs/import", {
          method: "POST",
          body: form,
        });
        onCreated(run.id);
      } else {
        let pdb: string | undefined,
          seq = sequence;
        if (workflow === "dock") {
          const form = new FormData();
          form.append("protein", new Blob([protein]), "protein.pdb");
          const prepared = await api<{ protein_pdb: string; sequence: string }>(
            "/v1/structures/protein",
            { method: "POST", body: form },
          );
          pdb = prepared.protein_pdb;
          seq = prepared.sequence;
        }
        const config = {
          ...settings,
          sequence: seq,
          ligand_smiles: smiles,
          protein_pdb: pdb,
          provider: workflow === "dock" ? "nvidia_diffdock" : "nvidia_nim",
          initial_samples: 2,
        };
        const run = await api<{ id: string }>("/v1/runs", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(config),
        });
        onCreated(run.id);
      }
    } catch (e) {
      setError(
        e instanceof Error ? e.message : "Could not start investigation",
      );
    } finally {
      setBusy(false);
    }
  }
  return (
    <div className="modal-backdrop">
      <section
        className="setup-modal"
        role="dialog"
        aria-modal="true"
        aria-labelledby="setup-title"
      >
        <div className="modal-heading">
          <div>
            <div className="eyebrow">NEW INVESTIGATION</div>
            <h2 id="setup-title">Investigate a molecular pose.</h2>
          </div>
          <button
            className="icon-button"
            aria-label="Close setup"
            onClick={onClose}
          >
            ×
          </button>
        </div>
        <form onSubmit={submit}>
          <label>
            Workflow
            <select
              value={workflow}
              onChange={(e) => {
                setWorkflow(e.target.value as Workflow);
                setError("");
              }}
            >
              <option value="review">
                Review existing predictions · PDB + SDF
              </option>
              <option value="dock">Generate poses · NVIDIA DiffDock</option>
              <option value="sequence">
                Sequence cofolding · experimental Boltz
              </option>
              <option value="demo">Synthetic engineering demo</option>
            </select>
          </label>
          {workflow !== "demo" ? (
            <>
              <label>
                Investigation name
                <input
                  value={name}
                  required
                  maxLength={100}
                  onChange={(e) => setName(e.target.value)}
                />
              </label>
              {workflow !== "sequence" ? (
                <>
                  <button
                    className="button secondary compact"
                    type="button"
                    disabled={busy}
                    onClick={() => void example()}
                  >
                    Load real example
                  </button>
                  <label>
                    Protein PDB{" "}
                    <span className="subtle">one chain · 100–400 residues</span>
                    <input
                      type="file"
                      accept=".pdb"
                      required={!protein}
                      onChange={(e) => void loadProtein(e.target.files?.[0])}
                    />
                  </label>
                  {inputLabel ? <p className="footnote">{inputLabel}</p> : null}
                  {workflow === "review" ? (
                    <label>
                      Ligand poses SDF{" "}
                      <span className="subtle">
                        1–4 poses of the same molecule
                      </span>
                      <input
                        type="file"
                        accept=".sdf,.mol"
                        multiple
                        required={!poses.length}
                        onChange={(e) => void loadPoses(e.target.files)}
                      />
                    </label>
                  ) : null}
                  {workflow === "review" && poses.length ? (
                    <p className="footnote">
                      {poses.length} ligand input file(s) ready
                    </p>
                  ) : null}
                </>
              ) : (
                <label>
                  Protein sequence
                  <textarea
                    rows={3}
                    value={sequence}
                    required
                    onChange={(e) => setSequence(e.target.value)}
                    spellCheck={false}
                  />
                </label>
              )}
              <label>
                Isomeric ligand SMILES{" "}
                <span className="subtle">10–50 heavy atoms</span>
                <input
                  value={smiles}
                  required
                  onChange={(e) => setSmiles(e.target.value)}
                  spellCheck={false}
                />
              </label>
              <label>
                Review decisions
                <select
                  value={policy}
                  onChange={(e) => setPolicy(e.target.value as typeof policy)}
                >
                  <option value="collective">Three-agent collective</option>
                  <option value="single_agent">Single agent</option>
                  <option value="rules">
                    Automated checks and refinement · no LLM
                  </option>
                  <option value="initial">
                    Checks only · no follow-up computation
                  </option>
                  <option value="fixed_hybrid">
                    Fixed sampling and refinement
                  </option>
                </select>
              </label>
              <label className="checkbox-label">
                <input
                  type="checkbox"
                  checked={extended}
                  onChange={(e) => setExtended(e.target.checked)}
                />
                Extended budget · up to 4 generated poses / 2 refinements
              </label>
              {exampleNote ? <p className="footnote">{exampleNote}</p> : null}
            </>
          ) : null}
          <div className="notice">
            <FlaskConical size={17} />
            <span>
              {workflow === "review"
                ? "Upload predictions from Boltz, AlphaFold or your docking pipeline. Structures are checked locally. Live agents receive measured evidence; optional refinement uses CPU OpenMM. Geometry is evaluated separately from experimental accuracy."
                : workflow === "dock"
                  ? "Protein and ligand inputs are sent to NVIDIA's sponsored DiffDock service. Two poses are generated in one request and checked for identity, geometry and diversity. No binding pocket or native ligand pose is supplied."
                  : workflow === "sequence"
                    ? "Experimental Boltz mode depends on exact CCD matching and has known ligand-export limitations. Use supplied structures or DiffDock for a robust workflow."
                    : "Artificial structures and authored mock decisions. No scientific result is claimed."}
            </span>
          </div>
          {error ? (
            <p className="error" role="alert">
              {error}
            </p>
          ) : null}
          <div className="modal-actions">
            <button
              className="button secondary"
              type="button"
              onClick={onClose}
            >
              Cancel
            </button>
            <button className="button" disabled={busy}>
              {busy ? "Preparing…" : "Start investigation"}
              <ArrowUpRight size={16} />
            </button>
          </div>
        </form>
      </section>
    </div>
  );
}
