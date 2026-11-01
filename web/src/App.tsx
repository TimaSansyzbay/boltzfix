import { useCallback, useEffect, useRef, useState } from "react";
import {
  Activity,
  ArrowDownToLine,
  ArrowRight,
  ArrowUpRight,
  Beaker,
  Check,
  ChevronRight,
  CircleDot,
  FlaskConical,
  GitBranch,
  Layers3,
  Microscope,
  Plus,
  ShieldCheck,
  Square,
  Users,
} from "lucide-react";
import { api, artifactUrl, demoConfig, ended, formatSeconds } from "./api";
import type { Candidate, Run, RunListItem, ScientificEvent } from "./api";
import { Viewer } from "./Viewer";
import { Setup } from "./Setup";
import { validateEvaluation } from "./evaluation";
import type { OfflineEvaluation } from "./evaluation";

type Tab = "overview" | "investigation" | "results";
const roleNames: Record<string, string> = {
  chemistry: "Chemistry",
  structure: "Structure",
  physics: "Physics",
  single: "Single agent",
};

function Budget({ run }: { run: Run | null }) {
  return (
    <div className="budget-grid">
      {[
        ["samples", "Prediction samples"],
        ["refinements", "Refinement attempts"],
        ["llm_calls", "LLM calls"],
        ["epochs", "Decision epochs"],
      ].map(([key, label]) => {
        const used =
            (run?.budget.used[key] ?? 0) + (run?.budget.reserved[key] ?? 0),
          limit =
            run?.budget.limits[key as keyof Run["budget"]["limits"]] ??
            (key === "samples"
              ? 2
              : key === "refinements"
                ? 1
                : key === "llm_calls"
                  ? 18
                  : 3);
        return (
          <div key={key} className="budget-item">
            <span>{label}</span>
            <strong>
              {used}
              <small> / {limit}</small>
            </strong>
            <div className="meter">
              <span
                style={{
                  width: `${100 * Math.min(1, used / Math.max(1, limit))}%`,
                }}
              />
            </div>
          </div>
        );
      })}
    </div>
  );
}

function CandidateList({
  run,
  active,
  onSelect,
}: {
  run: Run;
  active: string | undefined;
  onSelect: (id: string) => void;
}) {
  return (
    <div className="candidate-list">
      {run.candidates.map((c, i) => (
        <button
          key={c.id}
          className={`candidate-row ${active === c.id ? "active" : ""}`}
          onClick={() => onSelect(c.id)}
        >
          <span className="candidate-node">
            {c.kind === "minimized" ? (
              <GitBranch size={16} />
            ) : (
              <Layers3 size={16} />
            )}
          </span>
          <span className="candidate-copy">
            <strong>
              {c.kind === "minimized"
                ? "Refined child"
                : i === 0
                  ? c.provenance.provider === "imported"
                    ? "Imported pose"
                    : "Initial prediction"
                  : `Sample ${i + 1}`}
            </strong>
            <small>
              {c.diagnostics.severe_clashes} severe clashes ·{" "}
              {c.diagnostics.failed_physical} failed checks
            </small>
          </span>
          <span
            className={`status-dot ${c.diagnostics.physical_valid ? "good" : "warning"}`}
          />
          {run.selection?.candidate_id === c.id ? (
            <span className="tiny-tag">SELECTED</span>
          ) : null}
          <ChevronRight size={15} />
        </button>
      ))}
    </div>
  );
}

function Timeline({ events }: { events: ScientificEvent[] }) {
  const visible = events.filter((e) =>
    [
      "proposal",
      "review",
      "action_executed",
      "refinement_unavailable",
      "candidate_created",
      "selection_frozen",
      "disagreement",
      "status",
      "candidate_rejected",
    ].includes(e.type),
  );
  if (!visible.length)
    return (
      <div className="empty-panel">
        Agent proposals, challenges and measurements will appear here.
      </div>
    );
  return (
    <div className="timeline">
      {visible.map((e) => {
        const d = e.data;
        const role = typeof d.agent === "string" ? d.agent : "";
        const title =
          e.type === "proposal"
            ? `${roleNames[role] ?? role} proposes ${d.action}`
            : e.type === "review"
              ? `${roleNames[role] ?? role} ${d.endorse ? "endorses" : "challenges"} a proposal`
              : e.type === "action_executed"
                ? `Execute ${d.action}`
                : e.type.replaceAll("_", " ");
        const body = String(
          d.reason ??
            d.hypothesis ??
            d.message ??
            (d.candidate_id ? String(d.candidate_id) : (d.status ?? "")),
        );
        return (
          <article className={`timeline-event role-${role}`} key={e.seq}>
            <div className="timeline-marker">
              {e.type === "review" ? (
                <Users size={14} />
              ) : e.type === "selection_frozen" ? (
                <ShieldCheck size={14} />
              ) : (
                <CircleDot size={14} />
              )}
            </div>
            <div className="timeline-body">
              <div className="event-header">
                <strong>{title}</strong>
                <time>
                  {new Date(e.time).toLocaleTimeString([], {
                    hour: "2-digit",
                    minute: "2-digit",
                    second: "2-digit",
                  })}
                </time>
              </div>
              <p>{body}</p>
              {Array.isArray(d.evidence_ids) && d.evidence_ids.length ? (
                <div className="evidence-tags">
                  {d.evidence_ids.slice(0, 3).map((id) => (
                    <code key={String(id)}>
                      {String(id).split("/").slice(1).join("/")}
                    </code>
                  ))}
                  {d.evidence_ids.length > 3 ? (
                    <small>+{d.evidence_ids.length - 3} evidence</small>
                  ) : null}
                </div>
              ) : null}
            </div>
          </article>
        );
      })}
    </div>
  );
}

function ArtifactLinks({
  candidate,
  label,
}: {
  candidate: Candidate;
  label: string;
}) {
  return Object.entries(candidate.artifact_ids)
    .filter(([key]) =>
      ["protein", "ligand", "complex", "diagnostics", "physics"].includes(key),
    )
    .map(([key, id]) => (
      <a
        className="button secondary compact"
        key={key}
        aria-label={`${label} ${key}`}
        href={artifactUrl(id)}
      >
        {label === "Preview" ? `Preview ${key}` : key}
        <ArrowUpRight size={13} />
      </a>
    ));
}

export function Results({
  run,
  candidate,
}: {
  run: Run;
  candidate: Candidate | undefined;
}) {
  const frozenCandidate = run.candidates.find(
    (item) => item.id === run.selection?.candidate_id,
  );
  const exportCandidate = run.selection ? frozenCandidate : candidate;
  const previewDiffers =
    candidate && frozenCandidate && candidate.id !== frozenCandidate.id;
  const [evaluation, setEvaluation] = useState<OfflineEvaluation | null>(null),
    [evaluationError, setEvaluationError] = useState("");
  async function importEvaluation(file: File | undefined) {
    if (!file) return;
    try {
      if (file.size > 2 * 1024 * 1024)
        throw new Error("Evaluation files must be under 2 MB.");
      setEvaluation(
        await validateEvaluation(JSON.parse(await file.text()), run),
      );
      setEvaluationError("");
    } catch (e) {
      setEvaluationError(
        e instanceof Error ? e.message : "Invalid evaluation file",
      );
    }
  }
  return (
    <div className="results-layout">
      <section className="panel">
        <div className="panel-heading">
          <div>
            <div className="eyebrow">FROZEN SELECTION</div>
            <h2>What the evidence says</h2>
          </div>
          <ShieldCheck size={22} />
        </div>
        {run.selection ? (
          <>
            <div className="result-verdict">
              <span
                className={`status-dot ${run.selection.physical_valid ? "good" : "warning"}`}
              />
              <strong>
                {run.selection.candidate_id
                  ? run.selection.physical_valid
                    ? "Applicable physical checks passed"
                    : "Best available candidate has limitations"
                  : "No eligible candidate"}
              </strong>
            </div>
            <p className="body-copy">
              Experimental pose accuracy is scored separately, after this
              selection is frozen. Online diagnostics do not establish accuracy.
            </p>
            <ul className="limitations">
              {run.selection.limitations.map((l) => (
                <li key={l}>{l}</li>
              ))}
            </ul>
            <code className="receipt-hash">
              Config SHA-256 · {run.config_hash}
            </code>
          </>
        ) : (
          <p className="body-copy">
            The investigation has not committed a selection yet.
          </p>
        )}
        <p className="footnote">
          {run.selection
            ? "Frozen selection exports"
            : "Current candidate exports"}
          {exportCandidate ? ` · ${exportCandidate.id}` : ""}
        </p>
        <div className="download-row">
          <a className="button" href={`/v1/runs/${run.id}/export`}>
            <ArrowDownToLine size={16} />
            Run bundle
          </a>
          <a className="button secondary" href={`/v1/runs/${run.id}/report`}>
            Research report
          </a>
          <a
            className="button secondary"
            href={`/v1/runs/${run.id}/report?format=md`}
          >
            Report Markdown
          </a>
          {exportCandidate ? (
            <ArtifactLinks
              candidate={exportCandidate}
              label={run.selection ? "Selected" : "Candidate"}
            />
          ) : null}
        </div>
        {previewDiffers ? (
          <div className="preview-exports">
            <p className="footnote">
              Previewed candidate exports · {candidate.kind} · {candidate.id}
            </p>
            <div className="download-row">
              <ArtifactLinks candidate={candidate} label="Preview" />
            </div>
          </div>
        ) : null}
        {run.selection ? (
          <div className="offline-evaluation">
            <label className="button secondary compact">
              Import offline evaluation
              <input
                type="file"
                accept="application/json,.json"
                onChange={(e) => void importEvaluation(e.target.files?.[0])}
              />
            </label>
            <p className="footnote">
              Read locally after selection freeze. Reference data is never sent
              to agents.
            </p>
            {evaluationError ? (
              <p className="error" role="alert">
                {evaluationError}
              </p>
            ) : null}
            {evaluation ? (
              <div className="evaluation-reveal">
                <div className="eyebrow">
                  IMPORTED OFFLINE EVALUATION
                  {evaluation.synthetic ? " · SYNTHETIC TEST" : ""}
                </div>
                <strong>
                  {evaluation.selected.ligand_rmsd?.toFixed(3) ?? "Unavailable"}{" "}
                  Å ligand RMSD
                </strong>
                <p className="footnote">
                  Primary endpoint:{" "}
                  {evaluation.selected.primary_success
                    ? "passed"
                    : "not passed"}{" "}
                  · Protein Cα RMSD:{" "}
                  {evaluation.selected.protein_ca_rmsd?.toFixed(3) ??
                    "unavailable"}{" "}
                  Å
                </p>
              </div>
            ) : null}
          </div>
        ) : null}
      </section>
      <section className="panel">
        <div className="panel-heading">
          <div>
            <div className="eyebrow">REPRODUCIBILITY</div>
            <h2>Measured, recorded, bounded</h2>
          </div>
        </div>
        <dl className="metadata">
          <dt>Mode</dt>
          <dd>
            {run.synthetic
              ? "Synthetic fixture"
              : run.config.provider === "imported"
                ? "Imported structures / local scientific checks"
                : "Hosted scientific service"}
          </dd>
          <dt>Policy</dt>
          <dd>{run.config.policy}</dd>
          <dt>Provider</dt>
          <dd>{String(run.capabilities.name ?? run.config.provider)}</dd>
          <dt>Deployment version</dt>
          <dd>{String(run.capabilities.version ?? "not reported")}</dd>
          <dt>Elapsed time</dt>
          <dd>{formatSeconds(run.budget.wall_seconds)}</dd>
          <dt>Unresolved work</dt>
          <dd>{run.budget.outstanding_remote.length}</dd>
        </dl>
        <p className="footnote">
          Resource accounting compares hosted sample allowances and observed
          latency. Equal GPU time is not claimed.
        </p>
      </section>
    </div>
  );
}

export default function App() {
  const [runs, setRuns] = useState<RunListItem[]>([]),
    [run, setRun] = useState<Run | null>(null),
    [runId, setRunId] = useState<string | null>(() => {
      const requested = new URLSearchParams(window.location.search).get("run");
      return requested && /^run_[a-f0-9]{16}$/.test(requested)
        ? requested
        : null;
    });
  const [events, setEvents] = useState<ScientificEvent[]>([]),
    [tab, setTab] = useState<Tab>("overview"),
    [setup, setSetup] = useState(false),
    [error, setError] = useState("");
  const [activeId, setActiveId] = useState<string>(),
    [overlay, setOverlay] = useState(true),
    [clashes, setClashes] = useState(true),
    [busy, setBusy] = useState(false);
  const [backend, setBackend] = useState(false);
  const currentId = useRef(runId);
  currentId.current = runId;
  useEffect(() => {
    if (!runId) return;
    const url = new URL(window.location.href);
    url.searchParams.set("run", runId);
    window.history.replaceState(null, "", url);
  }, [runId]);
  const refreshRuns = useCallback(async () => {
    try {
      const list = await api<RunListItem[]>("/v1/runs");
      setRuns(list);
      setBackend(true);
      return list;
    } catch {
      setBackend(false);
      return [];
    }
  }, []);
  useEffect(() => {
    void refreshRuns().then((list) => {
      if (!currentId.current && list.length) setRunId(list[0].id);
    });
  }, [refreshRuns]);
  useEffect(() => {
    if (!runId) return;
    const id = runId;
    setRun(null);
    setEvents([]);
    setActiveId(undefined);
    setError("");
    let disposed = false;
    let refreshing = false;
    let finished = false;
    const refresh = async () => {
      if (refreshing) return;
      refreshing = true;
      try {
        const r = await api<Run>(`/v1/runs/${id}`);
        if (!disposed) {
          setRun(r);
          finished = ended.has(r.status);
          setBackend(true);
        }
      } catch (e) {
        if (!disposed)
          setError(e instanceof Error ? e.message : "Run unavailable");
      } finally {
        refreshing = false;
      }
    };
    void refresh();
    const progressTimer = window.setInterval(() => {
      if (!finished) void refresh();
    }, 3000);
    const stream = new EventSource(`/v1/runs/${id}/events`);
    stream.onmessage = (e) => {
      const event = JSON.parse(e.data) as ScientificEvent;
      if (!disposed) {
        setEvents((previous) =>
          previous.some((p) => p.seq === event.seq)
            ? previous
            : [...previous, event],
        );
        void refresh();
      }
    };
    stream.addEventListener("end", () => {
      stream.close();
      void refresh();
      void refreshRuns();
    });
    stream.onerror = () => {
      void refresh();
    };
    return () => {
      disposed = true;
      window.clearInterval(progressTimer);
      stream.close();
    };
  }, [runId, refreshRuns]);
  const candidate =
    run?.candidates.find((c) => c.id === activeId) ??
    run?.candidates.find((c) => c.id === run.selection?.candidate_id) ??
    run?.candidates.at(-1);
  const initial = run?.candidates[0];
  const loadingRun = Boolean(runId && !run);
  async function demo() {
    setBusy(true);
    setError("");
    try {
      const r = await api<{ id: string }>("/v1/runs", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(demoConfig),
      });
      setRunId(r.id);
      setTab("overview");
      void refreshRuns();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Cannot reach the backend");
    } finally {
      setBusy(false);
    }
  }
  async function cancel() {
    if (!run) return;
    try {
      await api(`/v1/runs/${run.id}/cancel`, { method: "POST" });
    } catch (e) {
      setError(String(e));
    }
  }
  return (
    <div className="app-shell">
      <aside className="sidebar">
        <a className="brand" href="/" aria-label="BoltzFix home">
          <span className="brand-mark">
            b<span>·</span>
          </span>
          <div>
            BoltzFix<small>SCIENTIFIC COLLECTIVE</small>
          </div>
        </a>
        <div className="workspace-label">RESEARCH WORKSPACE</div>
        <nav aria-label="Workspace navigation">
          {[
            ["overview", "Overview", Microscope],
            ["investigation", "Investigation", Users],
            ["results", "Results & artifacts", Layers3],
          ].map(([value, label, Icon]) => {
            const I = Icon as typeof Microscope;
            return (
              <button
                key={String(value)}
                className={tab === value ? "nav-item selected" : "nav-item"}
                onClick={() => setTab(value as Tab)}
              >
                <I size={18} />
                {String(label)}
                {tab === value ? <span className="nav-indicator" /> : null}
              </button>
            );
          })}
        </nav>
        <div className="sidebar-divider" />
        <div className="workspace-label run-heading">
          INVESTIGATIONS
          <button onClick={() => setSetup(true)} aria-label="New investigation">
            <Plus size={15} />
          </button>
        </div>
        <div className="run-nav">
          {runs.length ? (
            runs.map((r) => (
              <button
                key={r.id}
                className={r.id === runId ? "run-link active" : "run-link"}
                onClick={() => setRunId(r.id)}
              >
                <span
                  className={`status-dot ${r.status === "completed" ? "good" : ""}`}
                />
                <span>{r.name}</span>
                {r.synthetic ? <small>DEMO</small> : null}
              </button>
            ))
          ) : (
            <p>No investigations yet</p>
          )}
        </div>
        <div className="sidebar-bottom">
          <div className="collective-icon">
            <Users size={21} />
          </div>
          <strong>
            Three perspectives.
            <br />
            One testable decision.
          </strong>
          <p>Chemistry · Structure · Physics</p>
          <div className="connection">
            <span className={`status-dot ${backend ? "good" : ""}`} />
            {backend ? "Local backend connected" : "Backend offline"}
          </div>
          <span className="version">RESEARCH PROTOTYPE / v0.1</span>
        </div>
      </aside>
      <main>
        <header className="topbar">
          <div className="breadcrumb">
            Workspace
            <ChevronRight size={14} />
            <strong>
              {tab === "overview"
                ? "Overview"
                : tab === "investigation"
                  ? "Investigation"
                  : "Results & artifacts"}
            </strong>
          </div>
          <div className="topbar-right">
            <span className="private-tag">
              <ShieldCheck size={13} />
              Private workspace
            </span>
            <span className="avatar">BF</span>
          </div>
        </header>
        <div className="main-content">
          <div className="page-heading">
            <div>
              <div className="eyebrow">BUDGET-BOUNDED SCIENTIFIC REASONING</div>
              <h1>
                {tab === "overview"
                  ? "A better question for every pose."
                  : tab === "investigation"
                    ? "Follow the investigation."
                    : "Evidence you can inspect."}
              </h1>
              <p>Inspect. Challenge. Experiment. Select with evidence.</p>
            </div>
            <button className="button" onClick={() => setSetup(true)}>
              <Plus size={16} />
              New investigation
            </button>
          </div>
          <div className="compact-run-picker">
            <label htmlFor="investigation-picker">Investigation</label>
            <select
              id="investigation-picker"
              aria-label="Select investigation"
              value={runId ?? ""}
              onChange={(event) => setRunId(event.target.value)}
            >
              <option value="" disabled>
                Select an investigation
              </option>
              {runs.map((item) => (
                <option value={item.id} key={item.id}>
                  {item.name} · {item.status}
                </option>
              ))}
            </select>
          </div>
          <div className="mode-banner">
            <span className="mode-badge">
              <FlaskConical size={14} />
              {loadingRun
                ? "LOADING RUN"
                : run?.capabilities.archived_replay
                  ? "ARCHIVED REPLAY"
                  : run?.config.provider === "imported"
                    ? "IMPORTED REVIEW"
                    : run?.synthetic === false
                      ? "HOSTED MODE"
                      : "SYNTHETIC DEMO"}
            </span>
            <p>
              {loadingRun
                ? "Restoring the investigation and recorded artifacts."
                : run?.capabilities.archived_replay
                  ? "Archived structures and decisions. No new inference, model calls, or mechanics are being executed."
                  : run?.config.provider === "imported"
                    ? "Uploaded predictions, local diagnostics and bounded optional refinement. Experimental accuracy requires separate scoring."
                    : run?.synthetic === false
                      ? "Scientific execution requires configured sponsor access. Accuracy stays unclaimed until offline evaluation."
                      : "Artificial structures and authored agent decisions. No API keys, Boltz inference, or scientific improvement claimed."}
            </p>
            <a href="#method" onClick={() => setTab("results")}>
              View method
              <ArrowUpRight size={13} />
            </a>
          </div>
          {error ? (
            <div className="error-banner" role="alert">
              {error}
            </div>
          ) : null}
          <div className="run-bar">
            <div>
              <span
                className={`status-dot ${run?.status === "completed" ? "good" : "warning"}`}
              />
              <strong>
                {run?.config.name ??
                  (loadingRun
                    ? "Loading investigation…"
                    : "Ready for your first investigation")}
              </strong>
              <span className="status-label">
                {run?.status ?? (loadingRun ? "loading" : "not started")}
              </span>
            </div>
            <div>
              {run ? (
                <>
                  <span className="elapsed">
                    <Activity size={13} />
                    {formatSeconds(run.budget.wall_seconds)}
                  </span>
                  {!ended.has(run.status) ? (
                    <button
                      className="text-button"
                      onClick={() => void cancel()}
                    >
                      <Square size={12} />
                      Cancel
                    </button>
                  ) : null}
                </>
              ) : null}
              <button
                className="text-button"
                disabled={busy}
                onClick={() => void demo()}
              >
                {busy
                  ? "Starting…"
                  : run
                    ? "Run another demo"
                    : "Run synthetic example"}
                <ArrowRight size={15} />
              </button>
            </div>
          </div>
          <Budget run={run} />
          {run?.error ? (
            <div className="error-banner" role="alert">
              <strong>{String(run.error.code)}</strong> ·{" "}
              {String(run.error.message)}
            </div>
          ) : null}
          {tab === "overview" ? (
            <>
              <div className="overview-grid">
                <section className="panel structure-panel">
                  <div className="panel-heading">
                    <div>
                      <div className="eyebrow">STRUCTURE EXPLORER</div>
                      <h2>
                        {candidate
                          ? run?.selection?.candidate_id === candidate.id
                            ? "Selected candidate"
                            : candidate === initial
                              ? "Initial candidate"
                              : "Investigation candidate"
                          : "Protein–ligand complex"}
                      </h2>
                    </div>
                    <span
                      className={`pill ${candidate?.diagnostics.physical_valid ? "valid" : ""}`}
                    >
                      {candidate
                        ? candidate.diagnostics.physical_valid
                          ? "Physical checks passed"
                          : "Physical checks need attention"
                        : "Awaiting structure"}
                    </span>
                  </div>
                  <Viewer
                    candidate={candidate}
                    initial={initial}
                    overlay={overlay}
                    showClashes={clashes}
                    synthetic={run?.synthetic ?? true}
                  />
                  <div className="viewer-toolbar">
                    <div className="legend">
                      <span className="legend-dot protein" />
                      Protein
                      <span className="legend-dot selected" />
                      Candidate
                      <span className="legend-dot initial" />
                      Initial
                    </div>
                    <div className="viewer-toggles">
                      <label>
                        <input
                          type="checkbox"
                          checked={overlay}
                          onChange={(e) => setOverlay(e.target.checked)}
                        />
                        Overlay
                      </label>
                      <label>
                        <input
                          type="checkbox"
                          checked={clashes}
                          onChange={(e) => setClashes(e.target.checked)}
                        />
                        Clashes
                      </label>
                    </div>
                  </div>
                </section>
                <section className="panel evidence-panel">
                  <div className="panel-heading">
                    <div>
                      <div className="eyebrow">PREDICTION-ONLY EVIDENCE</div>
                      <h2>Pose health</h2>
                    </div>
                    <ShieldCheck size={21} />
                  </div>
                  {candidate ? (
                    <>
                      <div className="health-metrics">
                        <div>
                          <span>Severe clashes</span>
                          <strong
                            className={
                              candidate.diagnostics.severe_clashes
                                ? "amber-text"
                                : "green-text"
                            }
                          >
                            {candidate.diagnostics.severe_clashes}
                          </strong>
                        </div>
                        <div>
                          <span>Failed physical checks</span>
                          <strong>
                            {candidate.diagnostics.failed_physical}
                          </strong>
                        </div>
                        <div>
                          <span>
                            {candidate.diagnostics.inherited_confidence
                              ? "Inherited parent confidence"
                              : "Source confidence"}
                          </span>
                          <strong>
                            {candidate.diagnostics.confidence == null
                              ? "Not reported"
                              : candidate.diagnostics.confidence.toFixed(3)}
                          </strong>
                        </div>
                      </div>
                      <div className="checks-preview">
                        {candidate.diagnostics.checks
                          .filter((c) => c.status !== "pass")
                          .slice(0, 4)
                          .map((c) => (
                            <div key={c.id}>
                              <span className="status-dot warning" />
                              <span>
                                {c.id
                                  .replace("posebusters.", "")
                                  .replaceAll("_", " ")}
                              </span>
                              <small>{c.status}</small>
                            </div>
                          ))}
                        {candidate.diagnostics.physical_valid ? (
                          <div>
                            <Check size={15} />
                            <span>Applicable physical checks passed</span>
                          </div>
                        ) : null}
                      </div>
                      <p className="footnote">
                        Plausibility is not experimental accuracy. Confidence is
                        a model signal.
                      </p>
                    </>
                  ) : (
                    <div className="empty-panel">
                      <Beaker size={30} />
                      <p>
                        Geometry, chemistry and confidence checks appear after
                        the first candidate.
                      </p>
                    </div>
                  )}
                  <button
                    className="text-button"
                    onClick={() => setTab("results")}
                  >
                    Inspect all diagnostics
                    <ArrowUpRight size={14} />
                  </button>
                </section>
              </div>
              <div className="lower-grid">
                <section className="panel">
                  <div className="panel-heading">
                    <div>
                      <div className="eyebrow">CANDIDATE GENEALOGY</div>
                      <h2>Every attempt stays visible.</h2>
                    </div>
                    <GitBranch size={20} />
                  </div>
                  {run && run.candidates.length ? (
                    <CandidateList
                      run={run}
                      active={candidate?.id}
                      onSelect={setActiveId}
                    />
                  ) : (
                    <div className="empty-panel">
                      Parent structures, resamples and refinement children will
                      be preserved here.
                    </div>
                  )}
                </section>
                <section className="panel collective-card">
                  <div className="eyebrow">THE COLLECTIVE</div>
                  <h2>
                    A proposal has to survive
                    <br />a second perspective.
                  </h2>
                  <div className="agent-pills">
                    <span className="chemistry">C / Chemistry</span>
                    <span className="structure">S / Structure</span>
                    <span className="physics">P / Physics</span>
                  </div>
                  <p>
                    Independent proposals. Shared evidence. Two endorsements to
                    run an experiment.
                  </p>
                  <button
                    className="text-button"
                    onClick={() => setTab("investigation")}
                  >
                    Open the investigation
                    <ArrowUpRight size={14} />
                  </button>
                </section>
              </div>
            </>
          ) : tab === "investigation" ? (
            <div className="investigation-grid">
              <section className="panel">
                <div className="panel-heading">
                  <div>
                    <div className="eyebrow">AUDITABLE EVENT STREAM</div>
                    <h2>Proposals, challenges, outcomes.</h2>
                  </div>
                  <Users size={22} />
                </div>
                <Timeline events={events} />
              </section>
              <section className="panel sticky-panel">
                <div className="eyebrow">CANDIDATE BOARD</div>
                <h2>Shared scientific artifacts</h2>
                {run ? (
                  <CandidateList
                    run={run}
                    active={candidate?.id}
                    onSelect={(id) => {
                      setActiveId(id);
                      setTab("overview");
                    }}
                  />
                ) : null}
                <p className="footnote">
                  The resource gateway checks legality and budgets. Specialist
                  votes choose scientific actions.
                </p>
              </section>
            </div>
          ) : run ? (
            <>
              <Results key={run.id} run={run} candidate={candidate} />
              <section className="panel diagnostic-table">
                <div className="panel-heading">
                  <div>
                    <div className="eyebrow">DIAGNOSTIC CHECKLIST</div>
                    <h2>Unknown never means passed.</h2>
                    {candidate ? (
                      <p className="footnote">
                        Diagnostics for {candidate.kind} · {candidate.id}
                      </p>
                    ) : null}
                  </div>
                </div>
                <div className="table-scroll">
                  <table>
                    <thead>
                      <tr>
                        <th>Check</th>
                        <th>Category</th>
                        <th>Status</th>
                        <th>Evidence</th>
                      </tr>
                    </thead>
                    <tbody>
                      {candidate?.diagnostics.checks.map((c) => (
                        <tr key={c.id}>
                          <td>{c.id}</td>
                          <td>{c.category}</td>
                          <td>
                            <span className={`check-status ${c.status}`}>
                              {c.status}
                            </span>
                          </td>
                          <td>
                            {typeof c.value === "boolean"
                              ? String(c.value)
                              : typeof c.value === "number"
                                ? c.value
                                : c.detail || "—"}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </section>
            </>
          ) : (
            <section className="panel empty-panel">
              <FlaskConical size={30} />
              <h2>No results yet</h2>
              <p>
                Start an investigation to collect evidence and export a
                reproducible run.
              </p>
              <button
                className="button"
                disabled={busy}
                onClick={() => void demo()}
              >
                Run synthetic example
                <ArrowRight size={15} />
              </button>
            </section>
          )}
          <footer id="method">
            <span>
              BoltzFix / a scientific collective for protein–ligand pose
              investigation
            </span>
            <span>Evidence before claims.</span>
          </footer>
        </div>
      </main>
      {setup ? (
        <Setup
          onClose={() => setSetup(false)}
          onCreated={(id) => {
            setRunId(id);
            setSetup(false);
            setTab("overview");
            void refreshRuns();
          }}
        />
      ) : null}
    </div>
  );
}
