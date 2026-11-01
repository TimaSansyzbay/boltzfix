import { describe, it, expect } from "vitest";
import { validateEvaluation } from "./evaluation";
import fixture from "./fixtures/offline-evaluation.json";
import type { EvaluationRun } from "./evaluation";
const ready = { ...fixture.run, status: "completed" } satisfies EvaluationRun;
const failure = {
  ...fixture.failure_run,
  status: "failed",
} satisfies EvaluationRun;

describe("offline reference reveal boundary", () => {
  it("accepts an evaluation bound to a Python-generated frozen receipt", async () => {
    const result = await validateEvaluation(fixture.evaluation, ready);
    expect(result.selected.ligand_rmsd).toBe(0.2);
  });
  it("rejects active runs, changed selections and mismatched receipt hashes", async () => {
    await expect(
      validateEvaluation(fixture.evaluation, {
        ...ready,
        status: "running",
      }),
    ).rejects.toThrow("frozen");
    await expect(
      validateEvaluation(
        { ...fixture.evaluation, run_id: "another-run" },
        ready,
      ),
    ).rejects.toThrow("match");
    await expect(
      validateEvaluation(
        { ...fixture.evaluation, selection_receipt_hash: "changed" },
        ready,
      ),
    ).rejects.toThrow("hash");
  });
  it("keeps failed selections and non-ASCII limitation text verifiable", async () => {
    const result = await validateEvaluation(
      fixture.failure_evaluation,
      failure,
    );
    expect(result.selected.primary_success).toBe(false);
  });
});
