// The journey's browser: Chromium through the portal's own Playwright.
//
//   node browser.mjs sso-signin          --portal URL --label "Acme SSO" --email E --password P --shot F
//   node browser.mjs sso-wrong-password  --portal URL --label "Acme SSO" --email E --password P --shot F
//   node browser.mjs cli-authorize       --url CLI_LOGIN_URL --portal URL --label ... --email E --password P --shot F
//   node browser.mjs approve-vm-profile  --portal URL --token T --org O --app NAME --shot F
//   node browser.mjs ws-hold             --url WS_URL --seconds N
//
// Every command prints one JSON line and exits 0 when the outcome it
// checks for happened. PORTAL_DIR is the portal checkout whose
// node_modules provide @playwright/test.
import { createRequire } from 'node:module'

// Loaded only by the commands that open a page: ws-hold needs no browser.
const chromium = () => createRequire(`${process.env.PORTAL_DIR}/package.json`)('@playwright/test').chromium

const [command, ...rest] = process.argv.slice(2)
const opts = {}
for (let i = 0; i < rest.length; i += 2) opts[rest[i].replace(/^--/, '')] = rest[i + 1]

const TIMEOUT = 30000

function emit(result) {
  console.log(JSON.stringify(result))
  if (!result.ok) process.exitCode = 1
}

async function withPage(fn) {
  const browser = await chromium().launch()
  const context = await browser.newContext()
  const page = await context.newPage()
  const errors = []
  page.on('pageerror', (e) => errors.push(String(e).slice(0, 300)))
  const trail = []
  page.on('framenavigated', (frame) => {
    if (frame === page.mainFrame()) trail.push(frame.url().replace(/([?&](code|state|nonce|code_challenge)=)[^&]+/g, '$1…'))
  })
  try {
    const result = await fn(page, context)
    emit({ ...result, errors, trail })
  } catch (e) {
    const body = await page.innerText('body').catch(() => '')
    emit({ ok: false, error: String(e).slice(0, 500), url: page.url(), body: body.slice(0, 600), errors, trail })
  } finally {
    if (opts.shot) await page.screenshot({ path: opts.shot, fullPage: true }).catch(() => {})
    await browser.close()
  }
}

// The portal's sign-in page -> its SSO button -> Dex's login page, filled in.
async function throughDex(page, email, password) {
  await page.getByRole('button', { name: opts.label }).click({ timeout: TIMEOUT })
  await page.waitForURL((u) => u.pathname.startsWith('/dex/'), { timeout: TIMEOUT })
  const dexUrl = page.url()
  await page.locator('input[name="login"]').fill(email)
  await page.locator('input[name="password"]').fill(password)
  await Promise.all([page.waitForLoadState('domcontentloaded'), page.locator('button[type="submit"]').click()])
  return dexUrl
}

async function portalToken(page) {
  const origin = new URL(opts.portal).origin
  await page.waitForURL((u) => u.origin === origin && !u.pathname.startsWith('/login'), { timeout: TIMEOUT })
  await page.waitForFunction(() => localStorage.getItem('igni_token'), null, { timeout: TIMEOUT })
  return page.evaluate(() => localStorage.getItem('igni_token'))
}

const commands = {
  'sso-signin': () =>
    withPage(async (page) => {
      await page.goto(`${opts.portal}/login`)
      const dexUrl = await throughDex(page, opts.email, opts.password)
      const token = await portalToken(page)
      return { ok: Boolean(token), dex: new URL(dexUrl).origin + new URL(dexUrl).pathname, url: page.url(), token }
    }),

  // The negative case: Dex refuses, the browser never comes back signed in.
  'sso-wrong-password': () =>
    withPage(async (page) => {
      await page.goto(`${opts.portal}/login`)
      await throughDex(page, opts.email, opts.password)
      const stillAtDex = new URL(page.url()).pathname.startsWith('/dex/')
      const dexSays = (await page.innerText('body')).match(/invalid[^\n]*/i)?.[0] ?? ''
      // And the portal, asked directly, sends this browser to sign in.
      await page.goto(`${opts.portal}/dashboard`)
      await page.waitForURL((u) => u.pathname.startsWith('/login'), { timeout: TIMEOUT })
      const token = await page.evaluate(() => localStorage.getItem('igni_token'))
      return { ok: stillAtDex && Boolean(dexSays) && !token, stillAtDex, dexSays, portal: page.url(), token }
    }),

  // The CLI's sign-in, in a browser that has never signed in: /cli-auth sends
  // it to sign in, SSO brings it back, and the portal hands the CLI its code.
  'cli-authorize': () =>
    withPage(async (page) => {
      await page.goto(opts.url)
      await page.waitForURL((u) => u.pathname.startsWith('/login'), { timeout: TIMEOUT })
      const askedToSignIn = page.url()
      await throughDex(page, opts.email, opts.password)
      await page.waitForURL((u) => u.hostname === '127.0.0.1' && u.pathname === '/callback', { timeout: TIMEOUT })
      await page.waitForLoadState('domcontentloaded')
      const body = (await page.innerText('body').catch(() => '')).slice(0, 200)
      return { ok: true, askedToSignIn, callback: page.url().replace(/code=[^&]+/, 'code=…'), body }
    }),

  'approve-vm-profile': () =>
    withPage(async (page, context) => {
      // Signed in before any portal script runs, as the sign-in page leaves it.
      // (Setting it from a loaded /login page races that page's own
      // unauthenticated /portal/me, whose 401 handler clears the token.)
      await context.addInitScript(([t, o]) => {
        if (!sessionStorage.getItem('e2e-seeded')) {
          localStorage.setItem('igni_token', t)
          localStorage.setItem('igni_active_org', o)
          sessionStorage.setItem('e2e-seeded', '1')
        }
      }, [opts.token, opts.org])
      await page.goto(`${opts.portal}/vm-profiles`)
      await page.getByRole('button', { name: `Approve App ${opts.app}` }).click({ timeout: TIMEOUT })
      await page.getByText(`Approved: ${opts.app}`, { exact: false }).first().waitFor({ timeout: TIMEOUT })
      return { ok: true, url: page.url() }
    }),

  // Attach to an igni backend's WebSocket as a client window does, and stay.
  'ws-hold': async () => {
    const ws = new WebSocket(opts.url, ['igni.v1'])
    await new Promise((resolve, reject) => {
      ws.onopen = resolve
      ws.onerror = () => reject(new Error('WebSocket did not open'))
    })
    console.log(JSON.stringify({ ok: true, attached: ws.protocol }))
    await new Promise((resolve) => setTimeout(resolve, Number(opts.seconds) * 1000))
    ws.close()
  },
}

if (!commands[command]) {
  emit({ ok: false, error: `unknown command ${command}; one of ${Object.keys(commands).join(', ')}` })
} else {
  await commands[command]()
}
