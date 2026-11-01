import type { Run } from "./api";
export type EvaluationRun = Pick<
  Run,
  "id" | "status" | "synthetic" | "selection"
>;
export type OfflineEvaluation = {
  schema_version: "evaluation-v1";
  run_id: string;
  synthetic: boolean;
  selection_receipt_hash: string;
  selected: {
    candidate_id?: string;
    ligand_rmsd?: number;
    protein_ca_rmsd?: number;
    primary_success: boolean;
    accuracy_success: boolean;
  };
};
function canonical(value: unknown): unknown {
  if (Array.isArray(value)) return value.map(canonical);
  if (value && typeof value === "object")
    return Object.fromEntries(
      Object.entries(value)
        .sort(([a], [b]) => (a < b ? -1 : a > b ? 1 : 0))
        .map(([k, v]) => [k, canonical(v)]),
    );
  return value;
}
export async function validateEvaluation(
  raw: unknown,
  run: EvaluationRun,
): Promise<OfflineEvaluation> {
  if (
    !run.selection ||
    !["completed", "partial", "failed", "cancelled"].includes(run.status)
  )
    throw new Error("A frozen, ended investigation is required.");
  if (!raw || typeof raw !== "object")
    throw new Error("Invalid offline evaluation file.");
  const value = raw as OfflineEvaluation;
  if (
    value.schema_version !== "evaluation-v1" ||
    value.run_id !== run.id ||
    value.synthetic !== run.synthetic ||
    !value.selected ||
    (value.selected.candidate_id ?? null) !== run.selection.candidate_id
  )
    throw new Error("Evaluation does not match this frozen selection.");
  const bytes = new TextEncoder().encode(
    JSON.stringify(canonical(run.selection)).replace(
      /[\u007f-\uffff]/g,
      (character) =>
        "\\u" + character.charCodeAt(0).toString(16).padStart(4, "0"),
    ),
  );
  const hash = Array.from(
    new Uint8Array(await crypto.subtle.digest("SHA-256", bytes)),
    (b) => b.toString(16).padStart(2, "0"),
  ).join("");
  if (hash !== value.selection_receipt_hash)
    throw new Error("Evaluation receipt hash differs from this selection.");
  if (
    typeof value.selected.primary_success !== "boolean" ||
    typeof value.selected.accuracy_success !== "boolean" ||
    [value.selected.ligand_rmsd, value.selected.protein_ca_rmsd].some(
      (v) => v !== undefined && (!Number.isFinite(v) || v < 0),
    )
  )
    throw new Error("Invalid evaluation measurements.");
  return value;
}
