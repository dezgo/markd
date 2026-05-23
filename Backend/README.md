# Markd — Backend

Shared API for the native iOS + Android apps. The existing Flask web app in `../Web/` already exposes a `/todos` API, but the native clients will eventually want their own auth flow (device-keyed tokens rather than browser sessions), so this directory is reserved for a Cloudflare Workers + D1 backend matching the pattern used in `../../sudoku/Backend/`.

Not yet initialized. To start:

```bash
cd Backend
npm create cloudflare@latest -- --type hello-world --ts --no-deploy .
# then add D1, wrangler.toml, migrations/, src/index.ts modules per the sudoku reference
```

Until the Workers backend is up, the iOS and Android apps can hit the Flask API at `https://markd.appfoundry.cc` with the existing `X-API-Key` header (see `../Web/deploy/README.md`).
