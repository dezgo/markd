import type { Env } from "./types";
import { jsonOk, jsonError } from "./http";

export default {
  async fetch(req: Request, env: Env, _ctx: ExecutionContext): Promise<Response> {
    const url = new URL(req.url);
    const path = url.pathname;
    const method = req.method;

    if (path === "/health" && method === "GET") {
      return jsonOk({ ok: true, service: "markd-api", phase: 1 });
    }

    if (path === "/" && method === "GET") {
      return new Response(
        "markd-api: see ../Web/ for the production site; this worker is in scaffold (phase 1).\n",
        { headers: { "content-type": "text/plain; charset=utf-8" } },
      );
    }

    return jsonError(404, "not_found");
  },
} satisfies ExportedHandler<Env>;
