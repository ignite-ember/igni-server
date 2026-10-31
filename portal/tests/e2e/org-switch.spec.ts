/**
 * Org-switching E2E — switching the active org via the Sidebar's switcher
 * must cause subsequent API requests to send the new `X-Org-Id` header.
 * If this regresses, users see data for the wrong org.
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
  await page.addInitScript(() => {
    localStorage.setItem('igni_token', 'tok-e2e')
  })
}

const ME_TWO_ORGS = {
  user_id: 'u1',
  email: 'alice@x.com',
  full_name: 'Alice',
  created_at: '2026-01-01T00:00:00Z',
  role: 'admin',
  org_id: 'org-a',
  organizations: [
    { id: 'org-a', slug: 'a', display_name: 'Org A', role: 'admin' },
    { id: 'org-b', slug: 'b', display_name: 'Org B', role: 'member' },
  ],
}

test.describe('Org switching', () => {
  test('switching org changes the X-Org-Id header on subsequent requests', async ({ page }) => {
    const teamCalls: { orgId: string | null }[] = []

    await authenticate(page)
    await mockBackend(page, {
      'GET /v1/portal/me': (r) => r.fulfill(json(ME_TWO_ORGS)),
      'GET /v1/portal/usage': (r) =>
        r.fulfill(json({ data: [], total_tokens: 0, total_input_tokens: 0, total_output_tokens: 0, total_requests: 0 })),
      'GET /v1/portal/team': (r) => {
        teamCalls.push({ orgId: r.request().headers()['x-org-id'] ?? null })
        return r.fulfill(json({ members: [], total: 0 }))
      },
    })

    await page.goto('/dashboard')

    // The initial team query may fire before /portal/me hydrates the active
    // org (race between the two queries), so we only assert what's reliable:
    // after the user explicitly switches to Org B, a refetch goes out with
    // X-Org-Id: org-b. That's the regression we care about.
    await page.getByRole('button', { name: /Switch organization/i }).click()
    await page.getByRole('menuitemradio', { name: /Org B/i }).click()

    await expect.poll(() => teamCalls.map((c) => c.orgId).includes('org-b')).toBe(true)
  })

  test('active org persists across reloads (localStorage)', async ({ page }) => {
    await authenticate(page)
    let teamHeader: string | null = null
    await mockBackend(page, {
      'GET /v1/portal/me': (r) => r.fulfill(json(ME_TWO_ORGS)),
      'GET /v1/portal/usage': (r) =>
        r.fulfill(json({ data: [], total_tokens: 0, total_input_tokens: 0, total_output_tokens: 0, total_requests: 0 })),
      'GET /v1/portal/team': (r) => {
        teamHeader = r.request().headers()['x-org-id'] ?? null
        return r.fulfill(json({ members: [], total: 0 }))
      },
    })

    await page.goto('/dashboard')
    await page.getByRole('button', { name: /Switch organization/i }).click()
    await page.getByRole('menuitemradio', { name: /Org B/i }).click()

    // Wait for at least one /team request after the switch
    await expect.poll(() => teamHeader).toBe('org-b')

    // Reload — the stored org should be honored, not auto-reverted to the first
    teamHeader = null
    await page.reload()
    await expect.poll(() => teamHeader).toBe('org-b')
  })
})
