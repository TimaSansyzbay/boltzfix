import { describe, it, expect } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import { Results } from "./App";
import type { Candidate, Run } from "./api";

describe("frozen result provenance", () => {
  it("previewing a child cannot replace the selected structure download", () => {
    const base = {
      run_id: "run_example",
      action_id: "fixture",
      provenance: {},
      diagnostics: {
        version: "test",
        checks: [],
        chemically_eligible: true,
        physical_valid: true,
        failed_physical: 0,
        severe_clashes: 0,
        confidence_source: "fixture",
        inherited_confidence: false,
      },
    };
    const selected: Candidate = {
      ...base,
      id: "initial",
      kind: "prediction",
      artifact_ids: { ligand: "original-sdf" },
    };
    const child: Candidate = {
      ...base,
      id: "child",
      kind: "minimized",
      artifact_ids: { ligand: "refined-sdf" },
    };
    const run = {
      id: "run_example",
      candidates: [selected, child],
      selection: {
        candidate_id: "initial",
        physical_valid: true,
        limitations: [],
      },
      config_hash: "frozen",
      synthetic: false,
      config: { policy: "fixed_hybrid", provider: "nvidia_nim" },
      capabilities: {},
      budget: { wall_seconds: 10, outstanding_remote: [] },
    } as unknown as Run;
    const html = renderToStaticMarkup(<Results run={run} candidate={child} />);
    expect(html).toContain(
      'aria-label="Selected ligand" href="/v1/artifacts/original-sdf"',
    );
    expect(html).toContain(
      'aria-label="Preview ligand" href="/v1/artifacts/refined-sdf"',
    );
    expect(html).toContain("Frozen selection exports");
    expect(html).toContain("Previewed candidate exports");
    expect(run.selection?.candidate_id).toBe("initial");
  });
});
