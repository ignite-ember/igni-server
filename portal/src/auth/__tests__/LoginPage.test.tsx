/**
 * LoginPage — covers the password sign-in happy path, the code-fallback
 * UI toggle, and error surfacing for invalid credentials.
 */
import { describe, expect, test } from 'vitest'
import { http, HttpResponse } from 'msw'
import userEvent from '@testing-library/user-event'
import { screen } from '@testing-library/react'
import { Routes, Route } from 'react-router-dom'
import { renderWithProviders } from '../../test/render'
import { server } from '../../test/server'
import { LoginPage } from '../LoginPage'

function DashboardStub() {
  return <div data-testid="dashboard">DASHBOARD</div>
}

function renderLogin(route = '/login') {
  return renderWithProviders(
    <Routes>
      <Route path="/login" element={<LoginPage />} />
      <Route path="/dashboard" element={<DashboardStub />} />
    </Routes>,
    { route },
  )
}

describe('LoginPage', () => {
  test('renders heading + email + password fields', async () => {
    renderLogin()
    expect(await screen.findByRole('heading', { name: /Sign in to igni/i })).toBeInTheDocument()
    expect(screen.getByLabelText(/Email address/i)).toBeInTheDocument()
    expect(screen.getByLabelText(/Password/i)).toBeInTheDocument()
  })

  test('Sign in button disabled until both fields are typed', async () => {
    renderLogin()
    const btn = await screen.findByRole('button', { name: /^Sign in$/i })
    expect(btn).toBeDisabled()

    await userEvent.type(screen.getByLabelText(/Email address/i), 'a@b.com')
    expect(btn).toBeDisabled()
    await userEvent.type(screen.getByLabelText(/Password/i), 'pw')
    expect(btn).toBeEnabled()
  })

  test('successful sign-in stores token and navigates to /dashboard', async () => {
    server.use(
      http.post('/v1/sign-in', () => HttpResponse.json({ access_token: 'tok-1' })),
    )

    renderLogin()
    await userEvent.type(screen.getByLabelText(/Email address/i), 'a@b.com')
    await userEvent.type(screen.getByLabelText(/Password/i), 'pw')
    await userEvent.click(screen.getByRole('button', { name: /^Sign in$/i }))

    expect(await screen.findByTestId('dashboard')).toBeInTheDocument()
    expect(localStorage.getItem('igni_token')).toBe('tok-1')
  })

  test('surfaces "Invalid email or password" on 401', async () => {
    server.use(
      http.post('/v1/sign-in', () =>
        HttpResponse.json({ detail: 'Invalid email or password' }, { status: 401 }),
      ),
    )

    renderLogin()
    await userEvent.type(screen.getByLabelText(/Email address/i), 'a@b.com')
    await userEvent.type(screen.getByLabelText(/Password/i), 'bad')
    await userEvent.click(screen.getByRole('button', { name: /^Sign in$/i }))

    expect(await screen.findByText(/Invalid email or password/i)).toBeInTheDocument()
  })

  test('switching to code method sends OTP and moves to code step', async () => {
    server.use(
      http.post('/v1/send-sign-in-code', () => HttpResponse.json({ ok: true })),
    )

    renderLogin()
    await userEvent.click(screen.getByRole('button', { name: /Sign in with email code instead/i }))
    await userEvent.type(screen.getByLabelText(/Email address/i), 'a@b.com')
    await userEvent.click(screen.getByRole('button', { name: /Send sign-in code/i }))

    expect(await screen.findByRole('heading', { name: /Enter verification code/i })).toBeInTheDocument()
    expect(screen.getByLabelText(/Verification code/i)).toBeInTheDocument()
  })
})
