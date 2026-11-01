import { defineTool } from "eve/tools";
import { decide, requestSchema } from "../../src/decision.js";
import { publicDecisionFailure } from "../../src/token-budget.js";

export default defineTool({
  description:
    "One metered specialist proposal or peer review using supplied prediction-only evidence.",
  inputSchema: requestSchema,
  execute: async (input) => {
    try {
      return await decide(input);
    } catch (error) {
      return { error: publicDecisionFailure(error) };
    }
  },
});
