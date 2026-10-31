/**
 * Auth flow E2E — login, signup, and password reset against a fully-mocked
 * backend (`page.route('**\/v1/*', ...)`). The Vite dev server proxies /v1
 * to localhost:7777, but Playwright intercepts before that — so these tests
 * run with no backend dependency.
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

test.describe('LoginPage', () => {
  test('Sign in button is disabled until both fields are filled', async ({ page }) => {
    await page.goto('/login')
    const btn = page.getByRole('button', { name: /^Sign in$/i })
    await expect(btn).toBeDisabled()
    await page.getByLabel(/Email address/i).fill('alice@example.com')
    await expect(btn).toBeDisabled()
    await page.getByLabel(/Password/i).fill('pw')
    await expect(btn).toBeEnabled()
  })

  test('invalid credentials show an error banner', async ({ page }) => {
    await mockBackend(page, {
      'POST /v1/sign-in': (r) =>
        r.fulfill(json({ detail: 'Invalid email or password' }, 401)),
    })

    await page.goto('/login')
    await page.getByLabel(/Email address/i).fill('alice@example.com')
    await page.getByLabel(/Password/i).fill('wrong')
    await page.getByRole('button', { name: /^Sign in$/i }).click()

    await expect(page.getByText(/Invalid email or password/i)).toBeVisible()
  })

  test('successful sign-in navigates to /dashboard', async ({ page }) => {
    await mockBackend(page, {
      'POST /v1/sign-in': (r) =>
        r.fulfill(json({ access_token: 'tok-e2e' })),
      'GET /v1/portal/me': (r) =>
        r.fulfill(json({
          user_id: 'u1', email: 'alice@example.com', full_name: 'Alice',
          created_at: '2026-01-01T00:00:00Z', role: 'admin',
          org_id: 'org-1', organizations: [{ id: 'org-1', slug: 'demo', display_name: 'Demo', role: 'admin' }],
        })),
      'GET /v1/portal/usage': (r) =>
        r.fulfill(json({ data: [], total_tokens: 0, total_input_tokens: 0, total_output_tokens: 0, total_requests: 0 })),
      'GET /v1/portal/team': (r) =>
        r.fulfill(json({ members: [], total: 0 })),
    })

    await page.goto('/login')
    await page.getByLabel(/Email address/i).fill('alice@example.com')
    await page.getByLabel(/Password/i).fill('Str0ng!Pass')
    await page.getByRole('button', { name: /^Sign in$/i }).click()

    await expect(page).toHaveURL(/\/dashboard$/)
    await expect(page.getByRole('heading', { name: /Dashboard/i })).toBeVisible()
  })

  test('"Sign in with email code instead" toggles to code method', async ({ page }) => {
    await page.goto('/login')
    await page.getByRole('button', { name: /Sign in with email code instead/i }).click()
    await expect(page.getByRole('button', { name: /Send sign-in code/i })).toBeVisible()
  })

  test('"Create an account" link in footer navigates to /signup', async ({ page }) => {
    await page.goto('/login')
    await page.getByRole('link', { name: /Create an account/i }).click()
    await expect(page).toHaveURL(/\/signup$/)
  })

  test('"Forgot password" link navigates to /reset-password', async ({ page }) => {
    await page.goto('/login')
    await page.getByRole('link', { name: /Forgot password/i }).click()
    await expect(page).toHaveURL(/\/reset-password$/)
  })
})

test.describe('SignUpPage', () => {
  test('multi-step happy path lands on /welcome', async ({ page }) => {
    await mockBackend(page, {
      'POST /v1/send-sign-up-code': (r) => r.fulfill(json({ ok: true })),
      'POST /v1/sign-up': (r) => r.fulfill(json({ access_token: 'tok-signup' })),
      // Welcome page is auth-gated and reads /portal/me
      'GET /v1/portal/me': (r) =>
        r.fulfill(json({
          user_id: 'u1', email: 'new@x.com', full_name: 'New User',
          created_at: '2026-01-01T00:00:00Z', role: 'admin',
          org_id: 'org-1', organizations: [{ id: 'org-1', slug: 'demo', display_name: 'Demo', role: 'admin' }],
        })),
    })

    await page.goto('/signup')

    // Step 1: email
    await page.getByLabel(/Email address/i).fill('new@x.com')
    await page.getByRole('button', { name: /Send verification code/i }).click()

    // Step 2: verification code
    await expect(page.getByRole('heading', { name: /Enter verification code/i })).toBeVisible()
    await page.getByLabel(/Verification code/i).fill('123456')
    await page.getByRole('button', { name: /^Verify$/i }).click()

    // Step 3: profile details
    await expect(page.getByRole('heading', { name: /Complete your profile/i })).toBeVisible()
    await page.getByLabel(/First name/i).fill('New')
    await page.getByLabel(/Last name/i).fill('User')
    await page.getByLabel(/^Password$/i).fill('Str0ng!Pass')
    await page.getByLabel(/Confirm password/i).fill('Str0ng!Pass')
    await page.getByRole('button', { name: /Create account/i }).click()

    await expect(page).toHaveURL(/\/welcome$/)
  })

  test('weak password shows inline missing-rule errors', async ({ page }) => {
    await mockBackend(page, {
      'POST /v1/send-sign-up-code': (r) => r.fulfill(json({ ok: true })),
    })
    await page.goto('/signup')
    await page.getByLabel(/Email address/i).fill('a@b.com')
    await page.getByRole('button', { name: /Send verification code/i }).click()
    await page.getByLabel(/Verification code/i).fill('123456')
    await page.getByRole('button', { name: /^Verify$/i }).click()

    await page.getByLabel(/^Password$/i).fill('weak')
    await expect(page.getByText(/Missing:/i)).toBeVisible()
    await expect(page.getByText(/Missing:/i)).toContainText(/uppercase/i)
  })
})

test.describe('PasswordResetPage', () => {
  test('full reset flow shows success banner', async ({ page }) => {
    await mockBackend(page, {
      'POST /v1/send-reset-password-code': (r) => r.fulfill(json({ ok: true })),
      'POST /v1/reset-password': (r) => r.fulfill(json({ message: 'Password reset successfully' })),
    })

    await page.goto('/reset-password')
    await page.getByLabel(/Email address/i).fill('a@b.com')
    await page.getByRole('button', { name: /Send reset code/i }).click()

    await expect(page.getByLabel(/Reset code/i)).toBeVisible()
    await page.getByLabel(/Reset code/i).fill('123456')
    await page.getByRole('button', { name: /Continue/i }).click()

    await page.getByLabel(/New password/i).fill('Str0ng!Pass')
    await page.getByLabel(/Confirm password/i).fill('Str0ng!Pass')
    await page.getByRole('button', { name: /Reset password/i }).click()

    await expect(page.getByText(/Password reset successfully/i)).toBeVisible()
    await expect(page.getByRole('link', { name: /Back to sign in/i })).toBeVisible()
  })
})
