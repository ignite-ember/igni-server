/**
 * Team management E2E — exercises the TeamPage invite roundtrip:
 *  - admin types a valid email, picks a role, submits
 *  - request hits /portal/team/invite with the right payload
 *  - the new "invited" row appears in the member list
 *  - duplicate invite surfaces the 409 error
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

const ADMIN_ME = {
  user_id: 'admin-1',
  email: 'admin@x.com',
  full_name: 'Admin',
  created_at: '2026-01-01T00:00:00Z',
  role: 'admin',
  org_id: 'org-1',
  organizations: [{ id: 'org-1', slug: 'demo', display_name: 'Demo', role: 'admin' }],
}

const INITIAL_TEAM = {
  members: [
    {
      id: 'admin-1', user_id: 'admin-1', email: 'admin@x.com', full_name: 'Admin',
      role: 'admin', tier: 'pro', status: 'active',
      total_requests: 0, total_tokens: 0, last_active: null,
      joined_at: '2026-01-01T00:00:00Z', assigned_by: null,
    },
  ],
  total: 1,
}

test.describe('TeamPage invite flow', () => {
  test('admin invites a member; new row appears with "invited" status', async ({ page }) => {
    let capturedInvite: Record<string, unknown> = {}
    let inviteCount = 0
    // Refetch returns the new row after invite succeeds.
    const teamAfterInvite = {
      members: [
        ...INITIAL_TEAM.members,
        {
          id: 'inv-1', user_id: 'inv-1', email: 'newbie@x.com', full_name: null,
          role: 'member', tier: 'lite', status: 'invited',
          total_requests: 0, total_tokens: 0, last_active: null,
          joined_at: '2026-05-28T12:00:00Z', assigned_by: 'admin-1',
        },
      ],
      total: 2,
    }

    await authenticate(page)
    await mockBackend(page, {
      'GET /v1/portal/me': (r) => r.fulfill(json(ADMIN_ME)),
      'GET /v1/portal/team': (r) =>
        r.fulfill(json(inviteCount === 0 ? INITIAL_TEAM : teamAfterInvite)),
      'POST /v1/portal/team/invite': async (r) => {
        capturedInvite = (await r.request().postDataJSON()) as Record<string, unknown>
        inviteCount += 1
        return r.fulfill(
          json(
            {
              id: 'inv-1', user_id: 'inv-1', email: capturedInvite.email,
              full_name: null, role: capturedInvite.role, tier: 'lite', status: 'invited',
              total_requests: 0, total_tokens: 0, last_active: null,
              joined_at: '2026-05-28T12:00:00Z', assigned_by: 'admin-1',
            },
            201,
          ),
        )
      },
    })

    await page.goto('/team')

    await page.getByPlaceholder(/colleague@company.com/i).fill('newbie@x.com')
    await page.getByRole('button', { name: /Send invite/i }).click()

    // Request body matches the form
    await expect.poll(() => capturedInvite.email).toBe('newbie@x.com')
    expect(capturedInvite.role).toBe('member')

    // New row appears in the member table (success banner also mentions the
    // email; scope to the row that has the "Invited" badge to disambiguate).
    await expect(page.getByText('newbie@x.com', { exact: true })).toBeVisible()
    await expect(page.getByText(/^Invited$/i)).toBeVisible()
  })

  test('duplicate invite surfaces 409 detail in the feedback banner', async ({ page }) => {
    await authenticate(page)
    await mockBackend(page, {
      'GET /v1/portal/me': (r) => r.fulfill(json(ADMIN_ME)),
      'GET /v1/portal/team': (r) => r.fulfill(json(INITIAL_TEAM)),
      'POST /v1/portal/team/invite': (r) =>
        r.fulfill(json({ detail: 'A member with email already exists in this organization' }, 409)),
    })

    await page.goto('/team')

    await page.getByPlaceholder(/colleague@company.com/i).fill('admin@x.com')
    await page.getByRole('button', { name: /Send invite/i }).click()

    await expect(page.getByText(/already exists/i)).toBeVisible()
  })

  test('member (non-admin) sees the team list but no invite form', async ({ page }) => {
    await authenticate(page)
    await mockBackend(page, {
      'GET /v1/portal/me': (r) =>
        r.fulfill(json({ ...ADMIN_ME, role: 'member', organizations: [{ ...ADMIN_ME.organizations[0], role: 'member' }] })),
      'GET /v1/portal/team': (r) => r.fulfill(json(INITIAL_TEAM)),
    })

    await page.goto('/team')
    await expect(page.getByText('admin@x.com')).toBeVisible()
    await expect(page.getByPlaceholder(/colleague@company.com/i)).not.toBeVisible()
    await expect(page.getByRole('button', { name: /Send invite/i })).not.toBeVisible()
  })
})
