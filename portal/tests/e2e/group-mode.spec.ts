/**
 * Where igni runs for a group (design §6.1), end to end against a mocked API:
 *  - the card shows the group's mode and the three choices;
 *  - a change that disconnects nobody is PATCHed as `{mode}` and read back;
 *  - moving to "This computer only" warns first; Cancel sends nothing,
 *    confirming sends the change;
 *  - the Default group's mode can be changed, though nothing else on it can.
 */
import { test, expect, type Page } from '@playwright/test'

const ADMIN_ME = {
  user_id: 'admin-1',
  email: 'admin@x.com',
  full_name: 'Admin',
  created_at: '2026-01-01T00:00:00Z',
  role: 'admin',
  org_id: 'org-1',
  organizations: [{ id: 'org-1', slug: 'demo', display_name: 'Demo', role: 'admin' }],
}

function groupOf(mode: string, isDefault = false) {
  return {
    id: 'g-1',
    org_id: 'org-1',
    name: isDefault ? 'Default' : 'Engineering',
    description: null,
    default_model: null,
    theme: null,
    is_default: isDefault,
    mode,
    created_by_user_id: 'admin-1',
    created_at: '2026-01-01T00:00:00Z',
    updated_at: '2026-01-01T00:00:00Z',
    member_count: 4,
    entry_count: 0,
  }
}

const json = (body: object, status = 200) => ({
  status,
  contentType: 'application/json',
  body: JSON.stringify(body),
})

/** A group the API holds, changed by PATCH; every PATCH body is kept. */
async function mockGroup(page: Page, initial: string, { isDefault = false } = {}) {
  const state = { mode: initial, patches: [] as unknown[] }
  await page.addInitScript(() => {
    localStorage.setItem('igni_token', 'tok-e2e')
  })
  await page.route('**/v1/**', async (route) => {
    const url = new URL(route.request().url())
    const key = `${route.request().method()} ${url.pathname}`
    if (key === 'GET /v1/portal/me') return route.fulfill(json(ADMIN_ME))
    if (key === 'GET /v1/portal/groups/g-1') return route.fulfill(json(groupOf(state.mode, isDefault)))
    if (key === 'PATCH /v1/portal/groups/g-1') {
      const body = route.request().postDataJSON() as { mode: string }
      state.patches.push(body)
      state.mode = body.mode
      return route.fulfill(json(groupOf(state.mode, isDefault)))
    }
    if (key === 'GET /v1/portal/groups/g-1/entries') return route.fulfill(json({ entries: [], total: 0 }))
    if (key === 'GET /v1/portal/inference/models') return route.fulfill(json({ models: [] }))
    if (key === 'GET /v1/portal/licence/notice') return route.fulfill(json({ warnings: [] }))
    if (key === 'GET /v1/portal/deployment/notices') return route.fulfill(json({ notices: [] }))
    return route.fulfill({ status: 404, body: JSON.stringify({ detail: 'No mock for ' + key }) })
  })
  return state
}

function card(page: Page) {
  return page.locator('.settings-card', { hasText: 'Where igni runs for this group' })
}

async function choose(page: Page, current: string, choice: string) {
  await card(page).getByRole('button', { name: current }).click()
  await page.getByRole('menuitemradio', { name: new RegExp(`^${choice}`) }).click()
}

test.describe('where igni runs for a group', () => {
  test('a change that disconnects nobody is saved and read back', async ({ page }) => {
    const state = await mockGroup(page, 'local_and_cloud')
    await page.goto('/groups/g-1')

    await choose(page, 'This computer and the cloud', 'Cloud only')

    await expect(page.getByText(/move when their current run ends/)).toBeVisible()
    await expect(card(page).getByRole('button', { name: 'Cloud only' })).toBeVisible()
    expect(state.patches).toEqual([{ mode: 'cloud' }])
  })

  test('moving to this computer only warns first; Cancel saves nothing', async ({ page }) => {
    const state = await mockGroup(page, 'cloud')
    await page.goto('/groups/g-1')

    await choose(page, 'Cloud only', 'This computer only')
    const dialog = page.getByRole('alertdialog')
    await expect(dialog).toContainText(/disconnected at once/)
    await dialog.getByRole('button', { name: 'Cancel' }).click()

    await expect(dialog).toBeHidden()
    await expect(card(page).getByRole('button', { name: 'Cloud only' })).toBeVisible()
    expect(state.patches).toEqual([])
  })

  test('confirming the warning saves it', async ({ page }) => {
    const state = await mockGroup(page, 'cloud')
    await page.goto('/groups/g-1')

    await choose(page, 'Cloud only', 'This computer only')
    await page.getByRole('alertdialog').getByRole('button', { name: 'Switch off the cloud' }).click()

    await expect(card(page).getByRole('button', { name: 'This computer only' })).toBeVisible()
    expect(state.patches).toEqual([{ mode: 'local' }])
  })

  test("the Default group's mode can be changed, though its other settings cannot", async ({ page }) => {
    const state = await mockGroup(page, 'local_and_cloud', { isDefault: true })
    await page.goto('/groups/g-1')

    await expect(page.getByText('This is what igni ships')).toBeVisible()
    await choose(page, 'This computer and the cloud', 'Cloud only')

    await expect(card(page).getByRole('button', { name: 'Cloud only' })).toBeVisible()
    expect(state.patches).toEqual([{ mode: 'cloud' }])
  })
})
