import { defineConfig, devices } from '@playwright/test'

/**
 * E2E tests live under `tests/e2e/`. They spin up the Vite dev server on
 * port 5173 against a real (or stubbed) backend at port 7777.
 *
 * The suite intercepts every `/v1` call, so a backend is optional —
 * `tests/e2e/global-setup.ts` reports whether one answered and what
 * auth mode it is in, and only refuses the run when `PORTAL_URL` names
 * a deployment that is not there.
 */
export default defineConfig({
  testDir: './tests/e2e',
  fullyParallel: true,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 2 : 0,
  workers: process.env.CI ? 1 : undefined,
  // The skip reporter is not decoration: without it a run that skips
  // its integration coverage still ends in "23 passed". See F128.
  reporter: [[process.env.CI ? 'github' : 'list'], ['./tests/e2e/reporters/skips.ts']],
  // Checks the stack is up and says which auth mode it is in, before
  // any spec runs. F130.
  globalSetup: './tests/e2e/global-setup.ts',
  // A missing baseline must never write itself. Playwright's default
  // ('missing') would have a Linux runner *create* the baseline it has
  // none of and then pass — a visual suite that approves whatever it
  // first sees. Generating is deliberate: `--update-snapshots`. E4.
  updateSnapshots: 'none',
  use: {
    baseURL: process.env.PORTAL_URL ?? 'http://localhost:5173',
    trace: 'on-first-retry',
    screenshot: 'only-on-failure',
  },
  projects: [
    { name: 'chromium', use: { ...devices['Desktop Chrome'] } },
  ],
  // Always. This used to be `process.env.CI ? undefined : {…}`, on the
  // reasoning quoted at the top of this file — "in CI, set BACKEND_URL
  // to a deployed environment to skip the local server requirement".
  // Nothing sets BACKEND_URL, and the config does not read it, so in CI
  // there was no server at `baseURL` and every test in the suite failed
  // on a connection refused. Found by running the job's exact
  // environment in a container rather than by reading the file. E4.
  //
  // The suite is hermetic — every `/v1` call is intercepted — so what it
  // needs is the app served, not a backend, and `npm run dev` is the
  // same thing locally and in CI.
  //
  // The landing page and the docs used to need a second server built
  // with `VITE_PUBLIC_SITE=1`. They moved to their own repository
  // (igni-site), with their specs.
  webServer: {
    command: 'npm run dev',
    url: 'http://localhost:5173',
    reuseExistingServer: true,
    timeout: 120_000,
  },
})
