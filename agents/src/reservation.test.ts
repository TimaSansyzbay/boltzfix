import { test } from "node:test";
import assert from "node:assert/strict";
import { DatabaseSync } from "node:sqlite";
import { mkdtempSync, rmSync } from "node:fs";
import { join } from "node:path";
import { tmpdir } from "node:os";
import { claimReservation } from "./reservation.js";

test("a model request needs a real, unconsumed application reservation", () => {
  const root = mkdtempSync(join(tmpdir(), "boltzfix-gate-")),
    before = process.env.BOLTZFIX_DATA;
  process.env.BOLTZFIX_DATA = root;
  const db = new DatabaseSync(join(root, "runs.sqlite3"));
  db.exec(
    "CREATE TABLE actions(id TEXT,run_id TEXT,action TEXT,status TEXT,reservation TEXT,payload TEXT); CREATE TABLE runs(id TEXT,config TEXT,status TEXT,cancel INTEGER,selection TEXT,started REAL); CREATE TABLE agent_dispatches(action_id TEXT PRIMARY KEY,dispatched_at TEXT);",
  );
  const request = {
    reservation_id: "a",
    run_id: "r",
    role: "physics",
    phase: "propose",
    token_allowance: 5000,
    model_id: "claude-sonnet-4-6",
  };
  db.prepare("INSERT INTO runs VALUES(?,?,?,?,?,?)").run(
    "r",
    JSON.stringify({
      llm_model: request.model_id,
      budget: { wall_seconds: 900 },
    }),
    "running",
    0,
    null,
    Date.now() / 1000,
  );
  db.prepare("INSERT INTO actions VALUES(?,?,?,?,?,?)").run(
    "a",
    "r",
    "llm",
    "reserved",
    JSON.stringify({ llm_calls: 1, llm_tokens: 5000 }),
    JSON.stringify({ role: "physics", phase: "propose" }),
  );
  db.close();
  try {
    assert.throws(
      () => claimReservation({ ...request, reservation_id: "missing" }),
      /RESERVATION_REQUIRED/,
    );
    assert.throws(
      () => claimReservation({ ...request, token_allowance: 6000 }),
      /RESERVATION_INVALID/,
    );
    claimReservation(request);
    assert.throws(() => claimReservation(request), /UNIQUE constraint/);
  } finally {
    if (before === undefined) delete process.env.BOLTZFIX_DATA;
    else process.env.BOLTZFIX_DATA = before;
    rmSync(root, { recursive: true, force: true });
  }
});
