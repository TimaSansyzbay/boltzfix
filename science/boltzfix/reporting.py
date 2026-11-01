"""Deterministic, evidence-bound research reports. No extra model call required."""

from __future__ import annotations
import html
from .models import digest


def render_report(store, run_id, format="html"):
    run = store.summary(run_id)
    selected = next(
        (
            c
            for c in run.candidates
            if run.selection and c.id == run.selection.candidate_id
        ),
        None,
    )
    title = run.config.name
    verdict = (
        "No eligible selection"
        if not selected
        else (
            "Physical checks passed; experimental accuracy remains unverified"
            if selected.diagnostics.physical_valid
            else "Selected pose has physical issues requiring investigation"
        )
    )
    lines = [
        f"# BoltzFix research report: {title}",
        "",
        f"Run: {run.id}",
        f"Status: {run.status}",
        f"Assessment: {verdict}",
        "",
        "## Purpose",
        "Review protein–ligand poses, detect chemistry and geometry failures, coordinate bounded follow-up computations and preserve reproducible evidence. This report does not establish binding affinity, drug efficacy or clinical utility.",
        "",
        "## Inputs and method",
        f"- Protein residues: {len(run.config.sequence)}",
        f"- Ligand identity: {run.config.ligand_smiles}",
        f"- Workflow: {run.config.provider}",
        f"- Decision policy: {run.config.policy}",
        f"- Protocol: {run.config.protocol_version}",
        f"- Model: {run.config.llm_model if run.config.agent_mode=='live' else 'No live language-model decisions'}",
        f"- Configuration SHA-256: {run.config_hash}",
        "",
        "## Candidates",
        "Candidate | Kind | Parent | Eligible chemistry | Physical validity | Failed checks | Severe clashes | Confidence provenance",
        "--- | --- | --- | --- | --- | --- | --- | ---",
    ]
    for c in run.candidates:
        d = c.diagnostics
        confidence = (
            "not supplied"
            if d.confidence is None
            else f"{d.confidence:.4f} ({'inherited parent' if d.inherited_confidence else d.confidence_source})"
        )
        lines.append(
            f"{c.id}{' [SELECTED]' if selected and selected.id==c.id else ''} | {c.kind} | {c.parent_id or '—'} | {d.chemically_eligible} | {d.physical_valid} | {d.failed_physical} | {d.severe_clashes} | {confidence}"
        )
    lines += ["", "## Diagnostic findings"]
    findings = 0
    for c in run.candidates:
        for check in c.diagnostics.checks:
            if check.status not in {"pass", "not_applicable"}:
                findings += 1
                lines.append(
                    f"- {c.id}/{check.id}: {check.status}. {check.detail or check.value}"
                )
    if not findings:
        lines.append(
            "All applicable online checks passed. Their scope is physical plausibility, not experimental accuracy."
        )
    lines += ["", "## Sampling and uncertainty"]
    diversities = []
    for c in run.candidates:
        for other, value in (c.provenance.get("pose_diversity_angstrom") or {}).items():
            if c.kind == "prediction":
                diversities.append(value)
                lines.append(
                    f"- {c.id} versus {other}: {value:.4f} Å after protein alignment."
                )
    if not diversities:
        lines.append(
            "Only one pose, or no comparable prediction pair: sampling uncertainty is unresolved."
        )
    elif max(diversities) < 0.5:
        lines.append(
            "No meaningful pose diversity was observed. Repeated sampling is not evidence of independent convergence."
        )
    lines += [
        "Confidence is a model-specific signal. DiffDock scores are uncalibrated ranking scores, not probabilities. Minimized children inherit their parent confidence.",
        "",
        "## Agent investigation and executed actions",
    ]
    for e in store.events(run_id):
        d = e["data"]
        if e["type"] == "proposal":
            lines.append(
                f"- {e['time']} — {d.get('agent')}: proposes {d.get('action')}. Hypothesis: {d.get('hypothesis')}. Uncertainty: {d.get('uncertainty')}"
            )
        elif e["type"] == "review":
            lines.append(
                f"- {e['time']} — {d.get('agent')}: {'endorses' if d.get('endorse') else 'challenges'} {d.get('proposal_id')}. {d.get('reason')}"
            )
        elif e["type"] in {"refinement_unavailable", "candidate_rejected"}:
            lines.append(f"- {e['type']}: {d}")
    lines += ["", "## Physical computation"]
    physics = [c for c in run.candidates if c.kind == "minimized"]
    if not physics:
        lines.append(
            "No successful minimization is recorded. No MD trajectory is claimed."
        )
    for c in physics:
        p = c.provenance.get("physics", {})
        lines += [
            f"- Child {c.id}, parent {c.parent_id}.",
            f"- CPU restrained minimization: {p.get('latency_seconds',0):.2f} seconds.",
            f"- Same-system potential energy: {p.get('initial_energy_kj_mol')} → {p.get('final_energy_kj_mol')} kJ/mol.",
            f"- Local displacement protocol: {p.get('local_protocol')}.",
            "- Energy decrease and fewer clashes do not establish improved experimental pose accuracy.",
        ]
    lines += [
        "",
        "## Resource accounting",
        f"- Used: {run.budget.used}",
        f"- Outstanding reservations: {run.budget.reserved}",
        f"- Elapsed wall time: {run.budget.wall_seconds:.2f} seconds.",
        f"- Unresolved remote actions: {len(run.budget.outstanding_remote)}.",
        "Hosted request/pose allowances and observed latency are reported; equal GPU time is not claimed.",
        "",
        "## Reproducibility and limitations",
    ]
    if run.selection:
        lines += [
            f"Frozen selection receipt SHA-256: {digest(run.selection.model_dump())}",
            *["- " + x for x in run.selection.limitations],
        ]
        for key, value in run.selection.artifact_hashes.items():
            lines.append(f"- Selected {key} SHA-256: {value}")
    if run.error:
        lines.append(
            f"- Run error: {run.error.get('code')}: {run.error.get('message')}"
        )
    lines += [
        "- Experimental reference scoring is separate, after selection freeze. Import the matching evaluation JSON to view native-pose accuracy.",
        "- A physically valid pose may still occupy the wrong binding site or orientation.",
        "- Local minimization is useful for geometry, not for discovering a distant correct binding mode.",
        "- The run bundle contains structures, diagnostics, provenance, events and prepared physical systems when available.",
    ]
    markdown = "\n".join(lines) + "\n"
    if format == "md":
        return markdown
    parts = []
    table = False
    for line in lines:
        if line.startswith("--- | "):
            continue
        if " | " in line:
            if not table:
                parts.append("<table>")
                table = True
            parts.append(
                "<tr>"
                + "".join(
                    "<td>" + html.escape(cell.strip()) + "</td>"
                    for cell in line.split(" | ")
                )
                + "</tr>"
            )
            continue
        if table:
            parts.append("</table>")
            table = False
        if line.startswith("# "):
            parts.append("<h1>" + html.escape(line[2:]) + "</h1>")
        elif line.startswith("## "):
            parts.append("<h2>" + html.escape(line[3:]) + "</h2>")
        elif line:
            parts.append("<p>" + html.escape(line) + "</p>")
    if table:
        parts.append("</table>")
    return (
        '<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width"><title>'
        + html.escape(title)
        + " — BoltzFix</title><style>body{font:15px/1.65 system-ui;color:#173b32;max-width:1100px;margin:40px auto;padding:0 24px}h1{font-size:30px}h2{margin-top:32px;border-top:1px solid #ddd;padding-top:20px}p{overflow-wrap:anywhere}table{border-collapse:collapse;width:100%;font-size:12px}td{border:1px solid #ddd;padding:8px;overflow-wrap:anywhere}tr:first-child{background:#edf3ee;font-weight:bold}@media print{body{margin:0;font-size:11px}h2{break-after:avoid}tr{break-inside:avoid}}</style><body>"
        + "".join(parts)
        + "</body></html>"
    )
