/**
 * SignUpPage — 3-step flow: email → code → details.
 *
 *  - Step 1 sends a verification code via POST /send-sign-up-code.
 *  - Step 2 echoes the code back and gates step 3.
 *  - Step 3 collects name + password (5-rule validation) and POSTs /sign-up
 *    which returns an access_token; the page then calls login() and navigates.
 */
import { describe, expect, test } from 'vitest'
import { http, HttpResponse } from 'msw'
import userEvent from '@testing-library/user-event'
import { screen } from '@testing-library/react'
import { Routes, Route } from 'react-router-dom'
import { renderWithProviders } from '../../test/render'
import { server } from '../../test/server'
import { SignUpPage } from '../SignUpPage'

function WelcomeStub() {
  return <div data-testid="welcome">WELCOME</div>
}

function renderSignup(initialRoute = '/signup') {
  return renderWithProviders(
    <Routes>
      <Route path="/signup" element={<SignUpPage />} />
      <Route path="/welcome" element={<WelcomeStub />} />
    </Routes>,
    { route: initialRoute },
  )
}

describe('SignUpPage step 1 (email)', () => {
  test('renders heading + email field', async () => {
    renderSignup()
    expect(await screen.findByRole('heading', { name: /Create your igni account/i })).toBeInTheDocument()
    expect(screen.getByLabelText(/Email address/i)).toBeInTheDocument()
  })

  test('Send verification code button is disabled until email is typed', async () => {
    renderSignup()
    const btn = await screen.findByRole('button', { name: /Send verification code/i })
    expect(btn).toBeDisabled()

    await userEvent.type(screen.getByLabelText(/Email address/i), 'alice@example.com')
    expect(btn).toBeEnabled()
  })

  test('submitting email moves to code step', async () => {
    server.use(
      http.post('/v1/send-sign-up-code', () => HttpResponse.json({ ok: true })),
    )

    renderSignup()
    await userEvent.type(screen.getByLabelText(/Email address/i), 'alice@example.com')
    await userEvent.click(screen.getByRole('button', { name: /Send verification code/i }))

    expect(await screen.findByRole('heading', { name: /Enter verification code/i })).toBeInTheDocument()
    expect(screen.getByText(/alice@example\.com/i)).toBeInTheDocument()
  })
})

describe('SignUpPage step 3 (password validation)', () => {
  async function advanceToDetails() {
    server.use(
      http.post('/v1/send-sign-up-code', () => HttpResponse.json({ ok: true })),
    )
    renderSignup()
    await userEvent.type(screen.getByLabelText(/Email address/i), 'a@b.com')
    await userEvent.click(screen.getByRole('button', { name: /Send verification code/i }))
    await screen.findByRole('heading', { name: /Enter verification code/i })
    await userEvent.type(screen.getByLabelText(/Verification code/i), '123456')
    await userEvent.click(screen.getByRole('button', { name: /^Verify$/i }))
    await screen.findByRole('heading', { name: /Complete your profile/i })
  }

  test('shows inline missing-rule errors for a weak password', async () => {
    await advanceToDetails()

    // Type a password that misses uppercase + digit + special
    await userEvent.type(screen.getByLabelText(/^Password$/i), 'abcabcab')

    expect(await screen.findByText(/Missing:/i)).toBeInTheDocument()
    const validation = screen.getByText(/Missing:/i)
    expect(validation.textContent).toMatch(/uppercase/i)
    expect(validation.textContent).toMatch(/digit/i)
    expect(validation.textContent).toMatch(/special/i)
  })

  test('happy path: valid form POSTs /sign-up and navigates to /welcome', async () => {
    let captured: Record<string, unknown> = {}
    server.use(
      http.post('/v1/send-sign-up-code', () => HttpResponse.json({ ok: true })),
      http.post('/v1/sign-up', async ({ request }) => {
        captured = (await request.json()) as Record<string, unknown>
        return HttpResponse.json({ access_token: 'tok-abc' })
      }),
    )

    renderSignup()
    await userEvent.type(screen.getByLabelText(/Email address/i), 'alice@example.com')
    await userEvent.click(screen.getByRole('button', { name: /Send verification code/i }))
    await userEvent.type(await screen.findByLabelText(/Verification code/i), '123456')
    await userEvent.click(screen.getByRole('button', { name: /^Verify$/i }))

    await screen.findByRole('heading', { name: /Complete your profile/i })
    await userEvent.type(screen.getByLabelText(/First name/i), 'Alice')
    await userEvent.type(screen.getByLabelText(/Last name/i), 'Smith')
    await userEvent.type(screen.getByLabelText(/^Password$/i), 'Str0ng!Pass')
    await userEvent.type(screen.getByLabelText(/Confirm password/i), 'Str0ng!Pass')
    await userEvent.click(screen.getByRole('button', { name: /Create account/i }))

    expect(await screen.findByTestId('welcome')).toBeInTheDocument()
    expect(captured.email).toBe('alice@example.com')
    expect(captured.first_name).toBe('Alice')
    expect(captured.code).toBe('123456')
    expect(localStorage.getItem('igni_token')).toBe('tok-abc')
  })

  test('surfaces backend error from /sign-up', async () => {
    server.use(
      http.post('/v1/send-sign-up-code', () => HttpResponse.json({ ok: true })),
      http.post('/v1/sign-up', () =>
        HttpResponse.json({ detail: 'Invalid verification code' }, { status: 400 }),
      ),
    )

    renderSignup()
    await userEvent.type(screen.getByLabelText(/Email address/i), 'a@b.com')
    await userEvent.click(screen.getByRole('button', { name: /Send verification code/i }))
    await userEvent.type(await screen.findByLabelText(/Verification code/i), '000000')
    await userEvent.click(screen.getByRole('button', { name: /^Verify$/i }))

    await screen.findByRole('heading', { name: /Complete your profile/i })
    await userEvent.type(screen.getByLabelText(/First name/i), 'A')
    await userEvent.type(screen.getByLabelText(/Last name/i), 'B')
    await userEvent.type(screen.getByLabelText(/^Password$/i), 'Str0ng!Pass')
    await userEvent.type(screen.getByLabelText(/Confirm password/i), 'Str0ng!Pass')
    await userEvent.click(screen.getByRole('button', { name: /Create account/i }))

    expect(await screen.findByText(/Invalid verification code/i)).toBeInTheDocument()
  })

  test('rejects mismatched password confirmation client-side', async () => {
    await advanceToDetails()

    await userEvent.type(screen.getByLabelText(/First name/i), 'A')
    await userEvent.type(screen.getByLabelText(/Last name/i), 'B')
    await userEvent.type(screen.getByLabelText(/^Password$/i), 'Str0ng!Pass')
    await userEvent.type(screen.getByLabelText(/Confirm password/i), 'Different1!')
    await userEvent.click(screen.getByRole('button', { name: /Create account/i }))

    expect(await screen.findByText(/Passwords do not match/i)).toBeInTheDocument()
  })
})
