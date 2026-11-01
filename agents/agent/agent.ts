import { defineAgent } from "eve";
import { createAnthropic } from "@ai-sdk/anthropic";

// The custom channel invokes only the bounded decision tool. The Eve conversational
// loop is not the scientific coordinator and has no shell, file, browser or MCP tools.
export default defineAgent({
  model: createAnthropic()(process.env.BOLTZFIX_LLM_MODEL ?? "claude-opus-5-5"),
  modelContextWindowTokens: 1000000,
  defaultTools: false,
  tool: false,
});
