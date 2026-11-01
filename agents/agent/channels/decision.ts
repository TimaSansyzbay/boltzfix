import { defineChannel, POST, GET } from "eve/channels";
import { requestSchema } from "../../src/decision.js";

export default defineChannel({
  routes: [
    GET("/health", async () =>
      Response.json({
        status: "ok",
        runtime: "eve",
        credential_configured: !!process.env.ANTHROPIC_API_KEY,
        model: process.env.BOLTZFIX_LLM_MODEL ?? "claude-opus-5-5",
        live:
          process.env.BOLTZFIX_ALLOW_LIVE === "1" &&
          process.env.BOLTZFIX_SPONSOR_CONFIRMED === "1" &&
          !!process.env.ANTHROPIC_API_KEY,
      }),
    ),
    POST("/decision", async (request, { invokeTool, requestIp }) => {
      if (
        !requestIp ||
        !["127.0.0.1", "::1", "::ffff:127.0.0.1"].includes(requestIp) ||
        request.headers.has("origin")
      )
        return Response.json(
          { error: "PRIVATE_SERVICE_ONLY" },
          { status: 403 },
        );
      if (Number(request.headers.get("content-length") ?? 0) > 100000)
        return Response.json({ error: "BODY_TOO_LARGE" }, { status: 413 });
      const parsed = requestSchema.safeParse(await request.json());
      if (!parsed.success)
        return Response.json(
          { error: "INVALID_DECISION_REQUEST" },
          { status: 422 },
        );
      const result = await invokeTool("decide", parsed.data, {
        auth: {
          attributes: {},
          authenticator: "loopback",
          principalId: "science-worker",
          principalType: "service",
        },
      });
      if (result.outcome !== "completed")
        return Response.json(
          { error: "DECISION_UNAVAILABLE" },
          { status: 503 },
        );
      if (
        result.output &&
        typeof result.output === "object" &&
        "error" in result.output
      )
        return Response.json(result.output, { status: 503 });
      return Response.json(result.output);
    }),
  ],
});
