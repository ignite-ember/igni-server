# igni portal

The admin portal of igni server: React 19, TypeScript, Vite and Primer React.
It talks only to the igni server API, under `/v1`.

## Run it against a local API

From the repository root, with the API running (`make run`, see the root
README's Quick start):

```bash
cd portal && npm ci && cd ..
make portal-dev                    # http://localhost:3000, proxying /v1 to the API on :7777
make portal-dev PORT=7787 PORTAL_PORT=3010   # an API and a portal on other ports
```

The dev server proxies `/v1` to `IGNI_API_URL` (`make portal-dev` sets it from
`PORT`); `vite.config.ts` falls back to `http://localhost:7777`. The API's
`PORTAL_BASE_URL` must name the portal's address, since invitation and sign-in
links point there.

## Checks

The same ones CI runs:

```bash
npm run typecheck                  # tsc
npx eslint src --max-warnings 0
npm test -- --run                  # vitest
npx playwright test                # E2E; needs a stack: tools/portal-e2e-stack.sh
npm run build
```

Visual baselines are generated in a pinned Linux image
(`scripts/baselines-linux.sh`); see `docs/VISUAL_BASELINES.md`. Manual checks
are in `docs/PORTAL_MANUAL_QA.md`.
