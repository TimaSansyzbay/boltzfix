import type { components } from "./api.generated";
export type Run = components["schemas"]["RunSummary"];
export type Candidate = components["schemas"]["Candidate"];
export type RunConfig = components["schemas"]["RunConfig"];
export type ScientificEvent = {
  seq: number;
  type: string;
  time: string;
  data: Record<string, unknown>;
};
export type RunListItem = {
  id: string;
  name: string;
  status: string;
  synthetic: boolean;
};
export const artifactUrl = (id: string) =>
  `/v1/artifacts/${encodeURIComponent(id)}`;
export async function api<T>(path: string, options?: RequestInit): Promise<T> {
  const response = await fetch(path, options);
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    throw new Error(
      body.error?.message ??
        (Array.isArray(body.detail)
          ? body.detail.map((d: { msg: string }) => d.msg).join("; ")
          : body.detail) ??
        `Request failed (${response.status})`,
    );
  }
  return response.json() as Promise<T>;
}
export const DEMO_SMILES = "CC(=O)Oc1ccccc1C(=O)O";
export const DEMO_SEQUENCE = "A".repeat(100);
export const demoConfig: RunConfig = {
  name: "Synthetic pose investigation",
  case_id: "synthetic_demo",
  sequence: DEMO_SEQUENCE,
  ligand_smiles: DEMO_SMILES,
  provider: "mock",
  initial_samples: 1,
  policy: "collective",
  agent_mode: "synthetic",
  seed: 20261031,
  protocol_version: "boltzfix-v1",
  llm_model: "claude-opus-5-5",
  hybrid_schedule: "sample_then_minimize",
};
export const ended = new Set([
  "completed",
  "partial",
  "failed",
  "cancelled",
  "paused",
]);
export function formatSeconds(value: number) {
  return value < 60
    ? `${value.toFixed(1)}s`
    : `${Math.floor(value / 60)}m ${Math.round(value % 60)}s`;
}
