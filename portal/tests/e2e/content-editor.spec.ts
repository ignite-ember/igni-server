/**
 * The content editor loads with nothing but the portal's own origin.
 *
 * B34. `@monaco-editor/react` fetched Monaco from cdn.jsdelivr.net, so on
 * an air-gapped deployment the editor on the new-entry and edit-entry
 * pages never appeared. jsdom cannot show this — the unit suites replace
 * Monaco with a textarea — so this opens the page in a browser, refuses
 * every request that leaves the portal's origin, and checks the editor
 * still renders, highlights and takes input.
 */
import { test, expect, type Page } from '@playwright/test'

const ME = {
  user_id: 'u1',
  email: 'alice@x.com',
  full_name: 'Alice',
  created_at: '2026-01-01T00:00:00Z',
  role: 'admin',
  org_id: 'org-a',
  organizations: [{ id: 'org-a', slug: 'a', display_name: 'Org A', role: 'admin' }],
}

const GROUP = {
  id: 'g1',
  name: 'Platform',
  description: '',
  members: [],
  entries: [],
}

const json = (body: object, status = 200) => ({
  status,
  contentType: 'application/json',
  body: JSON.stringify(body),
})

/** Everything off the portal's origin is aborted and recorded. */
async function airGap(page: Page, baseURL: string): Promise<string[]> {
  const origin = new URL(baseURL).origin
  const outside: string[] = []
  await page.route('**/*', (route) => {
    const url = route.request().url()
    if (url.startsWith(origin) || url.startsWith('data:') || url.startsWith('blob:')) {
      return route.fallback()
    }
    outside.push(url)
    return route.abort('internetdisconnected')
  })
  await page.route('**/v1/**', (route) => {
    const path = new URL(route.request().url()).pathname
    if (path === '/v1/portal/me') return route.fulfill(json(ME))
    if (path === '/v1/portal/groups/g1') return route.fulfill(json(GROUP))
    if (path === '/v1/portal/agent-tools') return route.fulfill(json({ tools: ['read_file'] }))
    return route.fulfill(json({ detail: `no mock for ${path}` }, 404))
  })
  return outside
}

test('the content editor loads from the bundle, with no CDN', async ({ page, baseURL }) => {
  await page.addInitScript(() => {
    localStorage.setItem('igni_token', 'tok-e2e-editor')
  })
  const outside = await airGap(page, baseURL!)

  await page.goto('/groups/g1/entries/new')

  const editor = page.locator('.content-editor-frame .monaco-editor')
  await expect(editor).toBeVisible({ timeout: 30_000 })
  // Tokenised, not a bare textarea: the language registered from the bundle.
  await expect(editor.locator('.view-lines span[class*="mtk"]').first()).toBeVisible()

  await editor.locator('.view-lines').click()
  await page.keyboard.press('ControlOrMeta+End')
  await page.keyboard.press('Enter')
  // insertText, not type: per-key typing races Monaco's word suggestions.
  await page.keyboard.insertText('marker_typed_in_monaco')
  await expect(editor.locator('.view-lines')).toContainText('marker_typed_in_monaco')

  expect(outside, 'requests that left the portal origin').toEqual([])
})
