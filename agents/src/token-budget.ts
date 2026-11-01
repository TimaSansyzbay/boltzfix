/** Count the SDK's actual serialized request, including its native JSON schema. */
export class DecisionFailure extends Error {
  constructor(
    public code: string,
    message: string,
    public completionUnknown = false,
  ) {
    super(message);
  }
}

export function budgetedAnthropicFetch(
  allowance: number,
  fetcher: typeof fetch = globalThis.fetch,
) {
  let started = false;
  const state = {
    providerDispatched: false,
    failure: undefined as DecisionFailure | undefined,
    receipt: undefined as
      | {
          input_tokens_estimate: number;
          margin_tokens: number;
          max_output_tokens: number;
        }
      | undefined,
  };
  const boundedFetch: typeof fetch = async (input, init) => {
    try {
      if (started)
        throw new DecisionFailure(
          "RESERVATION_INVALID",
          "This agent request has already been attempted; no duplicate generation is permitted.",
        );
      started = true;
      const request = new Request(input, init);
      if (request.url !== "https://api.anthropic.com/v1/messages")
        throw new DecisionFailure(
          "AGENT_ENDPOINT",
          "Unexpected agent endpoint; request blocked.",
        );
      const body = (await request.json()) as Record<string, unknown>;
      const counted: Record<string, unknown> = {};
      for (const key of [
        "model",
        "messages",
        "system",
        "tools",
        "tool_choice",
        "thinking",
        "output_config",
        "cache_control",
        "speed",
      ])
        if (key in body) counted[key] = body[key];
      let response: Response;
      try {
        response = await fetcher(
          "https://api.anthropic.com/v1/messages/count_tokens",
          {
            method: "POST",
            headers: request.headers,
            body: JSON.stringify(counted),
            redirect: "error",
            signal: AbortSignal.any([
              request.signal,
              AbortSignal.timeout(15000),
            ]),
          },
        );
      } catch {
        throw new DecisionFailure(
          "TOKEN_COUNT_UNAVAILABLE",
          "Claude token-count preflight is unavailable; no generation was sent.",
        );
      }
      if (!response.ok)
        throw new DecisionFailure(
          "TOKEN_COUNT_UNAVAILABLE",
          `Claude token-count preflight returned HTTP ${response.status}; no generation was sent.`,
        );
      let count: number;
      try {
        count = ((await response.json()) as { input_tokens: number })
          .input_tokens;
        if (!Number.isSafeInteger(count) || count < 1) throw new Error();
      } catch {
        throw new DecisionFailure(
          "TOKEN_COUNT_UNAVAILABLE",
          "Claude returned an invalid token count; no generation was sent.",
        );
      }
      // Anthropic describes counts as estimates. Preserve a margin and meter
      // actual input + output usage against the same original reservation.
      const margin = Math.max(256, Math.ceil(count * 0.1));
      const maxOutput = Math.min(
        4096,
        Number(body.max_tokens),
        allowance - count - margin,
      );
      if (!Number.isSafeInteger(maxOutput) || maxOutput < 400)
        throw new DecisionFailure(
          "CONTEXT_BUDGET",
          "Remaining token allowance cannot fit the evidence and a bounded decision. Completed candidates are preserved; no generation was sent.",
        );
      state.receipt = {
        input_tokens_estimate: count,
        margin_tokens: margin,
        max_output_tokens: maxOutput,
      };
      body.max_tokens = maxOutput;
      // The scientific evidence, schema, model and instructions are unchanged.
      state.providerDispatched = true;
      return await fetcher(request.url, {
        method: request.method,
        headers: request.headers,
        body: JSON.stringify(body),
        redirect: "error",
        signal: request.signal,
      });
    } catch (error) {
      if (error instanceof DecisionFailure) state.failure = error;
      throw error;
    }
  };
  return { fetch: boundedFetch, state };
}

export function publicDecisionFailure(error: unknown) {
  if (error instanceof DecisionFailure)
    return {
      code: error.code,
      message: error.message,
      completion_unknown: error.completionUnknown,
    };
  const message = error instanceof Error ? error.message : "";
  const known: Record<string, string> = {
    LIVE_DISABLED: "Sponsor-backed agent access is not configured.",
    MODEL_CHANGED: "Configured agent model differs from the frozen run model.",
    RESERVATION_REQUIRED:
      "Agent request has no application resource reservation.",
    RESERVATION_INVALID: "Agent resource reservation is no longer eligible.",
    CONTEXT_TOO_LARGE:
      "Agent evidence exceeds the request size limit; nothing was truncated or sent.",
  };
  const code = Object.keys(known).find((key) => message.startsWith(key));
  if (code) return { code, message: known[code], completion_unknown: false };
  // Never return upstream response bodies, headers or credential-bearing errors.
  return {
    code: "AGENT_PROVIDER",
    message:
      "Agent provider did not return a verified decision; completion is unknown and no retry was made.",
    completion_unknown: true,
  };
}
