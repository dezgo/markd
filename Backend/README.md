# Markd — Backend

Cloudflare Workers + D1 API. Long-term, this **replaces** the Flask app in `../Web/` — both serve the same data to web + iOS + Android via one API. Mirror of [`../../sudoku/Backend/`](https://github.com/dezgo/sudoku) — same wrangler/D1/typescript conventions.

## Migration phases

| # | Phase | Branch | Status |
|---|---|---|---|
| 1 | Scaffold + D1 schema + `/health` endpoint | `backend/scaffold` | in progress |
| 2 | Auth endpoints (signup/login/verify/reset) | — | not started |
| 3 | `/todos` CRUD + push subscription endpoints | — | not started |
| 4 | Web UI: port Jinja templates → TS renderer, static asset serving, PWA service worker | — | not started |
| 5 | Web push delivery + scheduled cron (replaces `send_notifications.py` + `send_overdue_check.py`) | — | not started |
| 6 | Data migration script: dump `Web/markd.db` → insert into D1 | — | not started |
| 7 | DNS cutover: `markd.appfoundry.cc` → Workers; stop Flask service on the droplet | — | not started |

Until phase 7, this lives at the wrangler-issued URL (`markd-api.<account>.workers.dev`) and the production site stays on Flask at `../Web/`.

## Local dev

```bash
cd Backend
npm install
npm run db:migrate:local   # apply migrations to local D1
npm run dev                # http://localhost:8787
curl http://localhost:8787/health
```

## Deploy

```bash
npm run db:migrate:remote   # apply pending migrations to production D1
npm run deploy
curl https://markd-api.<account>.workers.dev/health
```

## Conventions

Matches `../../sudoku/Backend/`:
- D1 binding name: `DB`
- Migrations: `migrations/NNNN_<name>.sql`, sequential
- Module layout: `src/index.ts` (router), `src/types.ts` (Env), `src/http.ts` (jsonOk/jsonError), `src/crypto.ts`, `src/email.ts`, plus per-entity modules
- Manual routing in `index.ts` (no router library)
- Secrets set via `wrangler secret put NAME` (never in `wrangler.toml`)
- Timestamps: unix-ms `INTEGER` in D1
