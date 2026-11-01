import { test } from "node:test";
import assert from "node:assert/strict";
import {
  decide,
  promptFor,
  proposalSchema,
  requestSchema,
} from "./decision.js";

test("no credentials: decision fails before any provider request", async () => {
  const old = process.env.BOLTZFIX_ALLOW_LIVE;
  delete process.env.BOLTZFIX_ALLOW_LIVE;
  try {
    await assert.rejects(
      decide({
        role: "physics",
        phase: "propose",
        context: {},
        token_allowance: 5000,
        reservation_id: "unissued",
        run_id: "unissued",
      }),
      /LIVE_DISABLED/,
    );
  } finally {
    if (old !== undefined) process.env.BOLTZFIX_ALLOW_LIVE = old;
  }
});
test("oversized context is rejected instead of silently truncating evidence", () => {
  assert.throws(
    () =>
      promptFor({
        role: "chemistry",
        phase: "propose",
        context: { text: "x".repeat(81000) },
        token_allowance: 5000,
      }),
    /CONTEXT_TOO_LARGE/,
  );
});
test("bounded structured contracts exclude arbitrary tools and fields", () => {
  assert.equal(
    requestSchema.safeParse({
      role: "coordinator",
      phase: "propose",
      context: {},
      token_allowance: 5000,
    }).success,
    false,
  );
  assert.equal(proposalSchema.safeParse({ action: "shell" }).success, false);
  const p = promptFor({
    role: "structure",
    phase: "propose",
    context: {},
    token_allowance: 6000,
  });
  assert.ok(p.maxOutputTokens <= 4096);
  assert.match(p.system, /inherited/);
});
