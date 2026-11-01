import { DatabaseSync } from "node:sqlite";
import { resolve } from "node:path";

export function claimReservation(input: {
  reservation_id: string;
  run_id: string;
  role: string;
  phase: string;
  token_allowance: number;
  model_id: string;
}) {
  const database = resolve(
    process.env.BOLTZFIX_DATA ?? "../.boltzfix",
    "runs.sqlite3",
  );
  const db = new DatabaseSync(database, { open: true });
  try {
    db.exec("PRAGMA busy_timeout=10000; BEGIN IMMEDIATE;");
    const row = db
      .prepare(
        "SELECT a.status,a.reservation,a.payload,r.config,r.status AS run_status,r.cancel,r.selection,r.started FROM actions a JOIN runs r ON a.run_id=r.id WHERE a.id=? AND a.run_id=? AND a.action='llm'",
      )
      .get(input.reservation_id, input.run_id) as
      | {
          status: string;
          reservation: string;
          payload: string;
          config: string;
          run_status: string;
          cancel: number;
          selection: string | null;
          started: number;
        }
      | undefined;
    if (!row) throw new Error("RESERVATION_REQUIRED");
    const amounts = JSON.parse(row.reservation),
      payload = JSON.parse(row.payload),
      config = JSON.parse(row.config);
    if (
      row.status !== "reserved" ||
      row.cancel ||
      row.selection ||
      row.run_status !== "running" ||
      Date.now() / 1000 - row.started >= config.budget.wall_seconds ||
      amounts.llm_calls !== 1 ||
      amounts.llm_tokens !== input.token_allowance ||
      payload.role !== input.role ||
      payload.phase !== input.phase ||
      config.llm_model !== input.model_id
    )
      throw new Error("RESERVATION_INVALID");
    db.prepare(
      "INSERT INTO agent_dispatches(action_id,dispatched_at) VALUES(?,?)",
    ).run(input.reservation_id, new Date().toISOString());
    db.exec("COMMIT;");
  } catch (error) {
    try {
      db.exec("ROLLBACK;");
    } catch {}
    throw error;
  } finally {
    db.close();
  }
}
