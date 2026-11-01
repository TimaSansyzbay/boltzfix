import { describe, it, expect } from "vitest";
import { formatSeconds, artifactUrl, demoConfig, ended } from "./api";
describe("public scientific presentation contracts", () => {
  it("demo mode is explicit and cannot request live inference", () => {
    expect(demoConfig.provider).toBe("mock");
    expect(demoConfig.agent_mode).toBe("synthetic");
  });
  it("paused and partial runs are ended views", () => {
    expect(ended.has("paused")).toBe(true);
    expect(ended.has("running")).toBe(false);
  });
  it("download IDs are encoded and service latency stays readable", () => {
    expect(artifactUrl("a/b")).toBe("/v1/artifacts/a%2Fb");
    expect(formatSeconds(91)).toBe("1m 31s");
  });
});
