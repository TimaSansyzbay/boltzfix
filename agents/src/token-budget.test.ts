import { test } from "node:test";
import assert from "node:assert/strict";
import {
  budgetedAnthropicFetch,
  publicDecisionFailure,
  DecisionFailure,
} from "./token-budget.js";

const message = {
  model: "claude-opus-5-5",
  max_tokens: 4096,
  system: [{ type: "text", text: "Use measured evidence only." }],
  messages: [
    {
      role: "user",
      content: [{ type: "text", text: "Measured evidence ".repeat(500) }],
    },
  ],
  output_config: {
    effort: "low",
    format: {
      type: "json_schema",
      schema: { type: "object", properties: {}, additionalProperties: false },
    },
  },
  thinking: { type: "adaptive" },
};

test("large review boards use counted tokens, preserve all evidence and reduce output within the original allowance", async () => {
  const calls: { url: string; body: Record<string, unknown> }[] = [];
  const fetcher: typeof fetch = async (url, init) => {
    const body = JSON.parse(String(init?.body));
    calls.push({ url: String(url), body });
    return Response.json(
      String(url).endsWith("count_tokens")
        ? { input_tokens: 3000 }
        : { ok: true },
    );
  };
  const budget = budgetedAnthropicFetch(7390, fetcher);
  await budget.fetch("https://api.anthropic.com/v1/messages", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(message),
  });
  assert.equal(calls.length, 2);
  assert.deepEqual(calls[0].body.output_config, message.output_config);
  assert.deepEqual(calls[0].body.thinking, message.thinking);
  assert.deepEqual(calls[1].body.messages, message.messages);
  assert.equal(calls[1].body.max_tokens, 4090);
  assert.equal(budget.state.receipt?.input_tokens_estimate, 3000);
  await assert.rejects(
    budget.fetch("https://api.anthropic.com/v1/messages", {
      method: "POST",
      body: JSON.stringify(message),
    }),
    /already been attempted/,
  );
  assert.equal(calls.length, 2);
});

test("insufficient true allowance and token-count outages send no generation", async () => {
  for (const status of [200, 429]) {
    let calls = 0;
    const fetcher: typeof fetch = async (url) => {
      calls++;
      assert.ok(String(url).endsWith("count_tokens"));
      return Response.json({ input_tokens: 7200 }, { status });
    };
    const budget = budgetedAnthropicFetch(7390, fetcher);
    await assert.rejects(
      budget.fetch("https://api.anthropic.com/v1/messages", {
        method: "POST",
        body: JSON.stringify(message),
      }),
    );
    assert.equal(calls, 1);
    assert.equal(budget.state.providerDispatched, false);
    assert.equal(
      budget.state.failure?.code,
      status === 200 ? "CONTEXT_BUDGET" : "TOKEN_COUNT_UNAVAILABLE",
    );
  }
});

test("public failures expose typed local reasons and redact arbitrary upstream messages", () => {
  assert.equal(
    publicDecisionFailure(
      new DecisionFailure("CONTEXT_BUDGET", "Not enough allowance"),
    ).completion_unknown,
    false,
  );
  const result = publicDecisionFailure(new Error("upstream secret-body-value"));
  assert.ok(!JSON.stringify(result).includes("secret-body-value"));
  assert.equal(result.completion_unknown, true);
});
