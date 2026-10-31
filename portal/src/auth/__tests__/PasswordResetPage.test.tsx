/**
 * PasswordResetPage — 3-step reset flow: email → code → new password.
 *
 *  - POST /send-reset-password-code generates a code.
 *  - "Continue" advances to password step (no server call until submit).
 *  - POST /reset-password completes the flow and shows a success banner.
 */
import { describe, expect, test } from 'vitest'
import { http, HttpResponse } from 'msw'
import userEvent from '@testing-library/user-event'
import { screen } from '@testing-library/react'
import { renderWithProviders } from '../../test/render'
import { server } from '../../test/server'
import { PasswordResetPage } from '../PasswordResetPage'

describe('PasswordResetPage', () => {
  test('step 1 — Send reset code is disabled until email typed', async () => {
    renderWithProviders(<PasswordResetPage />, { route: '/reset-password' })

    const btn = await screen.findByRole('button', { name: /Send reset code/i })
    expect(btn).toBeDisabled()

    await userEvent.type(screen.getByLabelText(/Email address/i), 'a@b.com')
    expect(btn).toBeEnabled()
  })

  test('submitting email advances to code step', async () => {
    server.use(
      http.post('/v1/send-reset-password-code', () => HttpResponse.json({ ok: true })),
    )

    renderWithProviders(<PasswordResetPage />, { route: '/reset-password' })
    await userEvent.type(screen.getByLabelText(/Email address/i), 'a@b.com')
    await userEvent.click(screen.getByRole('button', { name: /Send reset code/i }))

    expect(await screen.findByLabelText(/Reset code/i)).toBeInTheDocument()
    expect(screen.getByText(/a@b\.com/i)).toBeInTheDocument()
  })

  test('weak password shows inline missing-rule errors', async () => {
    server.use(
      http.post('/v1/send-reset-password-code', () => HttpResponse.json({ ok: true })),
    )

    renderWithProviders(<PasswordResetPage />, { route: '/reset-password' })
    await userEvent.type(screen.getByLabelText(/Email address/i), 'a@b.com')
    await userEvent.click(screen.getByRole('button', { name: /Send reset code/i }))
    await userEvent.type(await screen.findByLabelText(/Reset code/i), '123456')
    await userEvent.click(screen.getByRole('button', { name: /Continue/i }))

    await userEvent.type(await screen.findByLabelText(/New password/i), 'short')
    expect(await screen.findByText(/Missing:/i)).toBeInTheDocument()
  })

  test('happy path: completes reset and shows success banner', async () => {
    server.use(
      http.post('/v1/send-reset-password-code', () => HttpResponse.json({ ok: true })),
      http.post('/v1/reset-password', () =>
        HttpResponse.json({ message: 'Password reset successfully' }),
      ),
    )

    renderWithProviders(<PasswordResetPage />, { route: '/reset-password' })
    await userEvent.type(screen.getByLabelText(/Email address/i), 'a@b.com')
    await userEvent.click(screen.getByRole('button', { name: /Send reset code/i }))
    await userEvent.type(await screen.findByLabelText(/Reset code/i), '123456')
    await userEvent.click(screen.getByRole('button', { name: /Continue/i }))

    await userEvent.type(await screen.findByLabelText(/New password/i), 'Str0ng!Pass')
    await userEvent.type(screen.getByLabelText(/Confirm password/i), 'Str0ng!Pass')
    await userEvent.click(screen.getByRole('button', { name: /Reset password/i }))

    expect(await screen.findByText(/Password reset successfully/i)).toBeInTheDocument()
    // Form replaced by "Back to sign in" link
    expect(screen.getByRole('link', { name: /Back to sign in/i })).toBeInTheDocument()
  })
})
