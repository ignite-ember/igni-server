/**
 * Protected-route coverage:
 *  - unauthenticated /dashboard redirects to /login
 *  - authenticated /welcome renders install commands + the dashboard button
 *  - authenticated /dashboard renders the stat cards
 *
 * Auth is faked by seeding `igni_token` in localStorage via
 * `page.addInitScript` so the AuthProvider hydrates as authenticated.
 */
import { test, expect, type Page, type Route } from '@playwright/test'

type RouteMap = Record<string, (route: Route) => Promise<void> | void>

async function mockBackend(page: Page, routes: RouteMap) {
  await page.route('**/v1/**', async (route) => {
    const url = new URL(route.request().url())
    const key = `${route.request().method()} ${url.pathname}`
    const handler = routes[key]
    if (handler) return handler(route)
    return route.fulfill({ status: 404, body: JSON.stringify({ detail: 'No mock for ' + key }) })
  })
}

const json = (body: object, status = 200) => ({
  status,
  contentType: 'application/json',
  body: JSON.stringify(body),
})

async function authenticate(page: Page) {
  // Seed the token BEFORE the app mounts so AuthProvider hydrates as authed.
  await page.addInitScript(() => {
    localStorage.setItem('igni_token', 'tok-e2e')
  })
}

/**
 * Signed out is the *server's* answer, not an absent localStorage key —
 * a deployment can authenticate by cookie or with PORTAL_AUTH_BYPASS,
 * and treating a missing key as signed out locked an admin out of a
 * working deployment. So these two tests have to say which answer they
 * mean, and make the server give it.
 *
 * They did not, and asserted the redirect against whatever the live
 * backend happened to think. Against the local dev stack — which runs
 * PORTAL_AUTH_BYPASS=1 — /portal/me answers 200, there is no such thing
 * as an unauthenticated visitor, and both failed. F130.
 */
async function serverSaysSignedOut(page: Page) {
  await page.route('**/v1/portal/me', (route) =>
    route.fulfill({ status: 401, contentType: 'application/json', body: '{"detail":"Not authenticated"}' }),
  )
}

test.describe('ProtectedRoute', () => {
  test('a visit the server does not recognise lands on /login', async ({ page }) => {
    await serverSaysSignedOut(page)
    await page.goto('/dashboard')
    await expect(page).toHaveURL(/\/login$/)
  })

  test('the same for a second protected route', async ({ page }) => {
    await serverSaysSignedOut(page)
    await page.goto('/settings')
    await expect(page).toHaveURL(/\/login$/)
  })

  test('a visit the server DOES recognise is let through without a token', async ({
    page,
  }) => {
    // The other half of the same rule, and the half that regressing
    // would lock every bypass or cookie deployment out of its own
    // portal. No token is seeded, on purpose: the 200 is the entire
    // reason this renders.
    //
    // Mocked rather than borrowed from whatever stack is running. The
    // two tests above used to read the ambient auth mode, which is why
    // they passed in CI (no backend, so "signed out") and failed
    // against a developer's stack (PORTAL_AUTH_BYPASS, so "signed
    // in"). A test that changes its mind depending on the machine is
    // not testing the product. F130.
    await page.route('**/v1/portal/me', (route) =>
      route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          user_id: 'u-bypass',
          email: 'demo@demo.invalid',
          full_name: 'Demo User',
          org_id: 'org-demo',
          role: 'admin',
        }),
      }),
    )
    await page.goto('/dashboard')
    await expect(page).toHaveURL(/\/dashboard$/)
    await expect(page.getByRole('heading', { level: 1 })).toBeVisible()
  })
})

test.describe('WelcomePage (authenticated)', () => {
  test('renders both install commands', async ({ page }) => {
    await authenticate(page)
    await mockBackend(page, {
      'GET /v1/portal/me': (r) =>
        r.fulfill(json({
          user_id: 'u1', email: 'alice@example.com', full_name: 'Alice',
          created_at: '2026-01-01T00:00:00Z', role: 'admin',
          org_id: 'org-1', organizations: [{ id: 'org-1', slug: 'demo', display_name: 'Demo', role: 'admin' }],
        })),
    })

    await page.goto('/welcome')

    // The product is igni. This said /Welcome to Ember Code/i and the page
    // says "Welcome to igni" — a spec that would have caught the rename, had
    // anything been running it.
    await expect(page.getByRole('heading', { name: /Welcome to igni/i })).toBeVisible()
    await expect(page.getByText(/brew install ignite-ember/i)).toBeVisible()
    await expect(page.getByText(/pip install ignite-ember/i)).toBeVisible()
  })

  test('the authenticate step is a command that runs', async ({ page }) => {
    // It was `ignite-ember /login`, presented in a copy-me command
    // block. `/login` is a slash command *inside* the session, so that
    // line exits with "Got unexpected extra argument (/login)" — the
    // first instruction a new account is given, and it fails.
    //
    // Asserted as "no command block passes /login as an argument"
    // rather than by matching the corrected text, because the next
    // person to reword this step should not have to update a test that
    // is really about the shape of the command.
    await authenticate(page)
    await mockBackend(page, {
      'GET /v1/portal/me': (r) =>
        r.fulfill(json({
          user_id: 'u1', email: 'alice@example.com', full_name: 'Alice',
          created_at: '2026-01-01T00:00:00Z', role: 'admin',
          org_id: 'org-1', organizations: [{ id: 'org-1', slug: 'demo', display_name: 'Demo', role: 'admin' }],
        })),
    })

    await page.goto('/welcome')

    const commands = await page.locator('.welcome-command, code, pre').allInnerTexts()
    const runnable = commands.map((c) => c.trim()).filter(Boolean)
    expect(runnable.length, 'no command blocks found on the welcome page').toBeGreaterThan(2)

    const slashAsArgument = runnable.filter((c) => /^\S*(igni|ignite-ember)\b.*\s\/login/.test(c))
    expect(slashAsArgument, 'these tell the user to run a slash command from the shell').toEqual([])
  })

  test('"Continue to dashboard" routes to /dashboard', async ({ page }) => {
    await authenticate(page)
    await mockBackend(page, {
      'GET /v1/portal/me': (r) =>
        r.fulfill(json({
          user_id: 'u1', email: 'a@b.com', full_name: 'A', created_at: '2026-01-01T00:00:00Z',
          role: 'admin', org_id: 'org-1', organizations: [{ id: 'org-1', slug: 'demo', display_name: 'Demo', role: 'admin' }],
        })),
      'GET /v1/portal/usage': (r) => r.fulfill(json({ data: [], total_tokens: 0, total_input_tokens: 0, total_output_tokens: 0, total_requests: 0 })),
      'GET /v1/portal/team': (r) => r.fulfill(json({ members: [], total: 0 })),
    })

    await page.goto('/welcome')
    await page.getByRole('button', { name: /Continue to dashboard/i }).click()
    await expect(page).toHaveURL(/\/dashboard$/)
  })
})

test.describe('DashboardPage (authenticated)', () => {
  test('renders welcome heading + stat cards', async ({ page }) => {
    await authenticate(page)
    await mockBackend(page, {
      'GET /v1/portal/me': (r) =>
        r.fulfill(json({
          user_id: 'u1', email: 'alice@example.com', full_name: 'Alice Chen',
          created_at: '2026-01-01T00:00:00Z', role: 'admin',
          org_id: 'org-1', organizations: [{ id: 'org-1', slug: 'demo', display_name: 'Demo', role: 'admin' }],
        })),
      'GET /v1/portal/usage': (r) =>
        r.fulfill(json({
          data: [{ date: '2026-05-01', input_tokens: 1000, output_tokens: 500, total_tokens: 1500, request_count: 7 }],
          total_tokens: 1_500_000, total_input_tokens: 1_000_000, total_output_tokens: 500_000, total_requests: 42,
        })),
      'GET /v1/portal/team': (r) =>
        r.fulfill(json({ members: [{ id: 'u1', user_id: 'u1', email: 'a@b.com', full_name: 'A', role: 'admin', tier: 'pro', status: 'active', total_requests: 0, total_tokens: 0, last_active: null, joined_at: '2026-01-01T00:00:00Z', assigned_by: null }], total: 1 })),
    })

    await page.goto('/dashboard')

    await expect(page.getByText(/Welcome, Alice Chen/i)).toBeVisible()
    await expect(page.getByText('Total Tokens')).toBeVisible()
    await expect(page.getByText(/1\.5M/)).toBeVisible()
    // Scoped to the stat card: "Requests" also appears as a section
    // heading and a chart label, and an unscoped getByText is a strict-mode
    // violation ("resolved to 2 elements") rather than a pass.
    await expect(page.locator('.stat-card-label', { hasText: 'Requests' })).toBeVisible()
    await expect(page.getByText('42')).toBeVisible()
  })
})
