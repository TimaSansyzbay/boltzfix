import { generateText, Output } from "ai";
import { createAnthropic } from "@ai-sdk/anthropic";
import { z } from "zod";
import { claimReservation } from "./reservation.js";
import { budgetedAnthropicFetch, DecisionFailure } from "./token-budget.js";

export const roleSchema = z.enum([
  "chemistry",
  "structure",
  "physics",
  "single",
]);
export const requestSchema = z
  .object({
    role: roleSchema,
    phase: z.enum(["propose", "review"]),
    context: z.record(z.string(), z.unknown()),
    token_allowance: z.number().int().min(2500).max(12000),
    model_id: z.string().min(1).max(100).default("claude-opus-5-5"),
    reservation_id: z.string().min(1),
    run_id: z.string().min(1),
  })
  .strict();
const evidence = z.array(z.string()).max(20);
export const proposalSchema = z
  .object({
    id: z.string(),
    agent: roleSchema,
    action: z.enum(["resample", "minimize", "stop"]),
    candidate_id: z.string().nullable(),
    samples: z.number().int().min(1).max(3),
    evidence_ids: evidence,
    hypothesis: z.string().max(1200),
    expected_benefit: z.string().max(1200),
    uncertainty: z.string().max(1200),
  })
  .strict();
export const reviewSchema = z
  .object({
    agent: z.enum(["chemistry", "structure", "physics"]),
    proposal_id: z.string(),
    endorse: z.boolean(),
    evidence_ids: evidence,
    reason: z.string().max(1200),
  })
  .strict();

export const responsibilities = {
  chemistry:
    "Inspect molecular identity, stereochemistry, bond/ring geometry and clashes. Challenge chemically inconsistent actions.",
  structure:
    "Inspect source confidence, candidate diversity, sampling coverage and diminishing returns. Propose bounded sampling experiments.",
  physics:
    "Inspect preparation feasibility, force fields, local displacement and same-system minimization results. Never use energy alone as pose accuracy.",
  single:
    "Use all chemistry, structure and physics evidence to choose the next action as the single-agent comparator.",
} as const;

export function promptFor(
  input: Pick<
    z.infer<typeof requestSchema>,
    "role" | "phase" | "context" | "token_allowance"
  >,
) {
  const system = `You are the BoltzFix ${input.role} specialist. ${responsibilities[input.role]}
You independently propose or review a bounded scientific action. There is no central scientific planner.
Use only supplied measured evidence. Cite IDs exactly as provided. Never invent measurements or experimental accuracy.
Actions: resample 1–3 candidates within remaining samples/requests; minimize an eligible original prediction once; stop.
Minimization is local restrained CPU mechanics, not MD or an affinity estimate. Missing tooling is evidence against refinement.
DiffDock position_confidence is an uncalibrated ranking score; negative values are valid, not probabilities. Do not compare confidence across backends. Confidence inherited by minimized children is not a new assessment. Lower clashes/confidence/energy do not prove native accuracy.
In review phase, endorse at most one listed proposal, or challenge one. Scientific agreement requires two specialists.
If multiple proposals request an identical action with identical parameters and compatible evidence, prefer the first such proposal to avoid splitting votes across duplicates. Still challenge unsupported evidence.
Keep concise evidence summaries, hypotheses and uncertainty; do not supply private chain-of-thought.
Return exactly the requested JSON schema. Use your assigned agent identity. Use a unique proposal ID. Never request external tools.`;
  const prompt = JSON.stringify({ phase: input.phase, ...input.context });
  // A byte limit controls request size, not token usage. The provider transport
  // counts the final SDK payload, including the schema, before generation.
  if (Buffer.byteLength(system + prompt) > 80000)
    throw new Error(
      "CONTEXT_TOO_LARGE: decision evidence exceeds request size limit",
    );
  return {
    system,
    prompt,
    maxOutputTokens: Math.min(4096, input.token_allowance),
  };
}

export async function decide(raw: unknown) {
  const input = requestSchema.parse(raw);
  if (
    process.env.BOLTZFIX_ALLOW_LIVE !== "1" ||
    process.env.BOLTZFIX_SPONSOR_CONFIRMED !== "1" ||
    !process.env.ANTHROPIC_API_KEY
  )
    throw new Error(
      "LIVE_DISABLED: sponsor-backed environment access is not configured",
    );
  const modelId = process.env.BOLTZFIX_LLM_MODEL ?? "claude-opus-5-5";
  if (modelId !== input.model_id)
    throw new Error(
      "MODEL_CHANGED: configured model differs from registered run",
    );
  const budget = budgetedAnthropicFetch(input.token_allowance);
  const provider = createAnthropic({
    apiKey: process.env.ANTHROPIC_API_KEY,
    fetch: budget.fetch,
  });
  const options = promptFor(input);
  claimReservation(input);
  const schema: z.ZodType<
    z.infer<typeof proposalSchema> | z.infer<typeof reviewSchema>
  > = input.phase === "propose" ? proposalSchema : reviewSchema;
  // Exactly one provider call: no hidden SDK retries, repairs, tool loops or fallbacks.
  const result = await generateText({
    model: provider(modelId),
    ...options,
    output: Output.object({ schema }),
    providerOptions: {
      anthropic: { effort: "low", structuredOutputMode: "outputFormat" },
    },
    maxRetries: 0,
    abortSignal: AbortSignal.timeout(110000),
  }).catch((error: unknown) => {
    if (budget.state.failure) throw budget.state.failure;
    throw new DecisionFailure(
      "AGENT_PROVIDER",
      "Claude did not return a verified decision; no automatic retry was made.",
      budget.state.providerDispatched,
    );
  });
  const tokens = result.totalUsage.totalTokens;
  if (tokens === undefined || tokens > input.token_allowance)
    throw new Error("USAGE_INVALID: no bounded usage receipt");
  const decision = schema.parse(result.output);
  if (decision.agent !== input.role)
    throw new Error("AGENT_SCHEMA: specialist identity mismatch");
  return {
    decision,
    model: result.response.modelId,
    usage: {
      total_tokens: tokens,
      input_tokens: result.totalUsage.inputTokens,
      output_tokens: result.totalUsage.outputTokens,
    },
    runtime: "eve-bounded-decision-tool",
    model_settings: {
      effort: "low",
      structured_output: "native_json_schema",
      max_output_tokens: budget.state.receipt?.max_output_tokens,
      token_preflight: budget.state.receipt,
    },
    cost_basis: "pre-reserved sponsor upper bound",
  };
}
