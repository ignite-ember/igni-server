/**
 * Does the thing actually happen?
 *
 * Every other suite here answers a narrower question. The unit tests
 * render a component against a mocked API. `accessibility.spec.ts`
 * replays *recorded* fixtures, so it proves a page can be drawn from a
 * known payload and nothing about whether the server would have sent
 * one. `visual.spec.ts` compares pixels. None of them presses a button
 * and then checks that the system changed.
 *
 * So this suite talks to the real API through the portal's own proxy,
 * performs a real action, and then **reloads the page** before
 * asserting. The reload is the point: optimistic UI, a cache that was
 * never invalidated, and a write that actually persisted all look
 * identical until you go back and ask the server again. A client
 * reporting "it didn't save" is describing exactly the difference
 * between those three, and it is invisible without the reload.
 *
 * Each test cleans up what it creates, and every name is prefixed so a
 * failed run leaves something obviously disposable rather than
 * something that looks real.
 *
 *   PORTAL_URL=http://127.0.0.1:3000 npx playwright test functional
 *
 * Requires a running API with a seeded org. Skipped otherwise rather
 * than failing, because "no backend" is not a product defect — but the
 * skip is declared, so it shows in the report instead of passing
 * quietly.
 */

import { generateKeyPairSync, randomUUID, sign, type KeyObject } from 'node:crypto'
import { test, expect, type Locator, type Page } from '@playwright/test'

/** The API itself, for what the portal's proxy does not pass on (`/.well-known`). */
const API = process.env.IGNI_API_URL ?? 'http://localhost:7777'

/** Prefix that marks a row as this suite's litter. */
const MARK = 'e2e-fn'

function unique(label: string): string {
  // Playwright forbids Date.now() nowhere, but a stable-ish suffix
  // keeps parallel runs from colliding on a unique-name constraint.
  return `${MARK} ${label} ${Math.random().toString(36).slice(2, 8)}`
}

/**
 * Click, and wait for the server to answer the write the click sent.
 * Every test here reloads straight after its action — the reload is the
 * point — and a reload that lands before the request has been answered
 * aborts it, so the page comes back showing the old value and the test
 * reports a write that never got the chance to happen.
 */
async function clickAndAwait(page: Page, button: Locator, method: string, path: RegExp): Promise<void> {
  await Promise.all([
    page.waitForResponse((r) => r.request().method() === method && path.test(new URL(r.url()).pathname)),
    button.click(),
  ])
}

test.use({ viewport: { width: 1440, height: 900 } })

test.beforeEach(async ({ page }) => {
  await page.addInitScript(() => {
    localStorage.setItem('igni_token', 'tok-e2e-functional')
  })
  // A backend that cannot answer /portal/me makes every assertion below
  // a statement about the network.
  const me = await page.request.get('/v1/portal/me').catch(() => null)
  test.skip(!me || !me.ok(), 'no API behind the portal — start the stack first')
})

test.describe('groups', () => {
  test('a created group survives a reload, and deleting it removes it', async ({ page }) => {
    const name = unique('group')

    await page.goto('/groups/new')
    await page.getByLabel(/^Name/i).first().fill(name)
    await page.getByRole('button', { name: /Create group/i }).click()

    // Landed somewhere that shows the group, rather than silently failing.
    await expect(page.getByText(name).first()).toBeVisible({ timeout: 10_000 })

    // The whole point: ask the server again.
    await page.goto('/groups')
    await page.reload()
    await expect(page.getByText(name).first()).toBeVisible({ timeout: 10_000 })

    // And clean up through the UI, which also exercises deletion.
    await page.getByText(name).first().click()
    const del = page.getByRole('button', { name: /^Delete/i }).first()
    if (await del.isVisible().catch(() => false)) {
      await del.click()
      const confirm = page.getByRole('button', { name: /^Delete/i }).last()
      await clickAndAwait(page, confirm, 'DELETE', /\/v1\/portal\/groups\/[^/]+$/)
      await page.goto('/groups')
      await page.reload()
      await expect(page.getByText(name)).toHaveCount(0, { timeout: 10_000 })
    }
  })

  test('creating a group with no name is refused, and nothing is created', async ({ page }) => {
    await page.goto('/groups')
    const before = await page.locator('body').innerText()

    await page.goto('/groups/new')
    await page.getByRole('button', { name: /Create group/i }).click()

    // Still on the form — a refusal that navigated away would have
    // created something.
    await expect(page.getByRole('button', { name: /Create group/i })).toBeVisible()

    await page.goto('/groups')
    await page.reload()
    // No new row appeared.
    const after = await page.locator('body').innerText()
    expect(after.split('\n').length).toBe(before.split('\n').length)
  })
})

test.describe('settings', () => {
  test('a changed display name persists', async ({ page }) => {
    await page.goto('/settings')
    const field = page.getByLabel(/Full name/i).first()
    // Strip any suffix a previous run left behind before deciding what
    // "original" is. Without this the restore is not idempotent: the
    // first run stored "Alex Chen", the second stored "Alex Chen
    // e2e-fn", and the seeded user ended up called "Alex Chen e2e-fn
    // e2e-fn" on the dashboard. A test that mutates real data has to
    // assume it has run before and crashed.
    const original = (await field.inputValue()).replace(new RegExp(`( ${MARK})+$`), '').trim()
    const changed = `${original} ${MARK}`

    await field.fill(changed)
    await clickAndAwait(page, page.getByRole('button', { name: /Save changes/i }), 'PATCH', /\/v1\/portal\/me$/)

    await page.reload()
    await expect(page.getByLabel(/Full name/i).first()).toHaveValue(changed, { timeout: 10_000 })

    // Put it back.
    await page.getByLabel(/Full name/i).first().fill(original)
    await clickAndAwait(page, page.getByRole('button', { name: /Save changes/i }), 'PATCH', /\/v1\/portal\/me$/)
    await page.reload()
    // Asserted, not assumed: an unverified cleanup is how the leak
    // above survived several runs unnoticed.
    await expect(page.getByLabel(/Full name/i).first()).toHaveValue(original)
    expect(original, 'the restored name still carries this suite’s marker').not.toContain(MARK)
  })
})

test.describe('navigation', () => {
  test('sidebar entries are real links, not click handlers', async ({ page, context }) => {
    // They were `<NavList.Item onClick={() => navigate(...)}>`, which
    // Primer renders as an `<a>` with no `href`. Left-click worked and
    // nothing else did — and cmd-click was worse than dead: it
    // navigated the *current* tab, so a reflex that should open a
    // second view instead threw away the one you were looking at.
    //
    // Asserted through the browser rather than the DOM, because
    // "has an href" is not the property anybody cares about.
    await page.goto('/dashboard')
    await page.waitForLoadState('networkidle')

    const hrefs = await page.locator('.sidebar-root a').evaluateAll((els) =>
      els.map((el) => el.getAttribute('href')),
    )
    expect(hrefs.length, 'no sidebar anchors found').toBeGreaterThan(4)
    expect(hrefs.filter((h) => !h), 'sidebar entries with no href').toEqual([])

    const before = context.pages().length
    await page.locator('.sidebar-root a', { hasText: 'Team' }).first()
      // Cmd on macOS, Ctrl elsewhere: on Linux, Meta-click is not
      // "open in a new tab", and CI runs on Linux.
      .click({ modifiers: ['ControlOrMeta'] })
    await page.waitForTimeout(1000)

    expect(context.pages().length, 'cmd-click opened no new tab').toBeGreaterThan(before)
    expect(page.url(), 'cmd-click navigated the tab it was supposed to leave alone')
      .toContain('/dashboard')
  })

  test('every sidebar entry reaches a page that renders', async ({ page }) => {
    // The cheapest question nobody asks: does each nav item go
    // somewhere that is not a blank screen or an error boundary?
    await page.goto('/dashboard')
    // Every internal link in the sidebar root. Not `nav a` — the
    // sidebar is a `div.sidebar-root`, and scoping to `<nav>` found
    // nothing at all, which the first version reported as a failure of
    // the app rather than of the selector.
    const links = await page.locator('.sidebar-root a[href^="/"]').evaluateAll((els) =>
      Array.from(new Set(els.map((el) => el.getAttribute('href') ?? ''))).filter(Boolean),
    )
    expect(links.length, 'no sidebar links found').toBeGreaterThan(4)

    const broken: string[] = []
    for (const href of links) {
      await page.goto(href)
      await page.waitForLoadState('networkidle')
      const text = (await page.locator('main, body').first().innerText()).trim()
      const hasHeading = await page.locator('h1, h2').first().isVisible().catch(() => false)
      if (text.length < 40 || !hasHeading) broken.push(`${href} (${text.length} chars)`)
      if (/something went wrong|unexpected error/i.test(text)) broken.push(`${href} (error boundary)`)
    }

    expect(broken, 'these nav destinations render nothing useful').toEqual([])
  })

  test('the organisation switcher lists the orgs the API reports', async ({ page }) => {
    const me = await (await page.request.get('/v1/portal/me')).json()
    const orgs: string[] = (me.organizations ?? []).map((o: { display_name: string }) => o.display_name)
    test.skip(orgs.length === 0, 'no organisations on this account')

    await page.goto('/dashboard')
    await page.getByRole('button', { name: /Switch organization/i }).click()

    for (const name of orgs) {
      await expect(page.getByText(name).first()).toBeVisible()
    }
  })
})

test.describe('VM profile requests', () => {
  /** A `private_key_jwt` client assertion, as an app signs one with its own key. */
  function assertion(appId: string, audience: string, key: KeyObject): string {
    const b64 = (value: object) => Buffer.from(JSON.stringify(value)).toString('base64url')
    const now = Math.floor(Date.now() / 1000)
    const claims = { iss: appId, sub: appId, aud: audience, iat: now, exp: now + 60, jti: randomUUID() }
    const body = `${b64({ alg: 'RS256', typ: 'JWT' })}.${b64(claims)}`
    return `${body}.${sign('sha256', Buffer.from(body), key).toString('base64url')}`
  }

  test("an app's request is approved by the admin and becomes its profile", async ({ page }) => {
    const me = await (await page.request.get('/v1/portal/me')).json()
    const orgId: string = me.org_id
    const name = unique('bot')
    const { publicKey, privateKey } = generateKeyPairSync('rsa', { modulusLength: 2048 })
    const jwk = { ...publicKey.export({ format: 'jwk' }), use: 'sig', alg: 'RS256' }
    const registered = await page.request.post('/v1/portal/apps', {
      data: { name, owner_user_id: me.user_id, jwks: { keys: [jwk] } },
    })
    expect(registered.ok(), await registered.text()).toBe(true)
    const appId: string = (await registered.json()).app_id
    let profileId: string | null = null
    try {
      // The app, as itself: `client_credentials` with its own key.
      const discovery = await (await page.request.get(`${API}/.well-known/openid-configuration`)).json()
      const issued = await page.request.post('/v1/oauth/token', {
        form: {
          grant_type: 'client_credentials',
          client_assertion_type: 'urn:ietf:params:oauth:client-assertion-type:jwt-bearer',
          client_assertion: assertion(appId, discovery.issuer, privateKey),
        },
      })
      expect(issued.ok(), await issued.text()).toBe(true)
      const app = { Authorization: `Bearer ${(await issued.json()).access_token}` }
      const asked = await page.request.put(`/v1/apps/${appId}/vm-profile-request`, {
        headers: app,
        data: { vcpus: 2, packages: { npm: ['@playwright/mcp@0.0.84'] }, setup_script: 'true\n' },
      })
      expect(asked.status(), await asked.text()).toBe(201)

      // Nothing runs before the admin approves: the VM still has the default.
      const before = await (await page.request.get(`/v1/apps/${appId}/vm-profile`, { headers: app })).json()
      expect(before.packages.npm).toEqual([])

      await page.goto('/vm-profiles')
      await expect(page.getByLabel(`Setup script of App ${name}`)).toHaveText('true')
      await clickAndAwait(page, page.getByRole('button', { name: `Approve App ${name}` }), 'POST', /\/vm-profile-requests\/[^/]+\/approve$/)

      await page.reload()
      await expect(page.getByText(new RegExp(`Approved: .*App ${name}`))).toBeVisible({ timeout: 10_000 })
      await expect(page.getByRole('button', { name: `Approve App ${name}` })).toHaveCount(0)
      const after = await (await page.request.get(`/v1/apps/${appId}/vm-profile`, { headers: app })).json()
      expect(after.profile_name).toBe(`App: ${name}`.slice(0, 60))
      expect(after.packages.npm).toEqual(['@playwright/mcp@0.0.84'])
      profileId = after.profile_id
    } finally {
      await page.request.delete(`/v1/portal/apps/${appId}`)
      if (profileId) await page.request.delete(`/v1/portal/orgs/${orgId}/vm-profiles/${profileId}`)
    }
  })
})
