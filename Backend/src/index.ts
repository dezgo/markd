import type { Env } from "./types";
import { jsonOk, jsonError } from "./http";
import { authStart, authVerify } from "./auth";

export default {
  async fetch(req: Request, env: Env, _ctx: ExecutionContext): Promise<Response> {
    const url = new URL(req.url);
    const path = url.pathname;
    const method = req.method;

    if (path === "/health" && method === "GET") {
      return jsonOk({ ok: true, service: "markd-api", phase: 2 });
    }

    if (path === "/v1/auth/start" && method === "POST") return authStart(req, env);
    if (path === "/v1/auth/verify" && method === "POST") return authVerify(req, env);

    if (path === "/" && method === "GET") {
      return new Response(
        "markd-api: see ../Web/ for the production site; this worker is in scaffold (phase 2).\n",
        { headers: { "content-type": "text/plain; charset=utf-8" } },
      );
    }

    return jsonError(404, "not_found");
  },
} satisfies ExportedHandler<Env>;
