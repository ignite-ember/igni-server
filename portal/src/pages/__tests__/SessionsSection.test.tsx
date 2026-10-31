/**
 * The Settings → Sessions panel. DEC-10/B5.
 *
 * Four things, and the fourth is the one worth having: a *failed*
 * revocation must not sign the person out. The success path calls
 * `logout()`, and the obvious way to write that — clear the token
 * whatever happens, since we are about to be refused anyway — tells
 * somebody their sessions were withdrawn when nothing was written. They
 * would sign back in and carry on with a token that still works, which is
 * the exact opposite of what they came here for.
 */
import { describe, expect, test, vi } from 'vitest'
import { http, HttpResponse } from 'msw'
import userEvent from '@testing-library/user-event'
import { screen, within } from '@testing-library/react'
import { renderWithProviders } from '../../test/render'
import { server } from '../../test/server'
import { SessionsSection } from '../SettingsPage'

const REVOKE = '/v1/portal/me/revoke-sessions'

function signedIn() {
  localStorage.setItem('igni_token', 'a-token')
}

describe('SessionsSection', () => {
  test('says that the CLI and editors stop working too', async () => {
    // The one sentence that changes what somebody expects afterwards. A
    // panel that only mentioned "this browser" would leave them
    // believing a paired editor kept its access.
    renderWithProviders(<SessionsSection />)

    expect(screen.getByRole('button', { name: /Sign out everywhere/i })).toBeEnabled()
    expect(screen.getByText(/CLI/)).toBeInTheDocument()
  })

  test('asks before doing anything', async () => {
    let calls = 0
    server.use(
      http.post(REVOKE, () => {
        calls += 1
        return HttpResponse.json({ user_id: 'u1', tokens_valid_after: '2026-09-03T12:00:01Z' })
      }),
    )
    renderWithProviders(<SessionsSection />)

    await userEvent.click(screen.getByRole('button', { name: /Sign out everywhere/i }))

    // The dialog is open and nothing has been sent. A confirm dialog
    // wired to fire on open would still look right in a screenshot.
    expect(await screen.findByText(/You will be signed out here as well/i)).toBeInTheDocument()
    expect(calls).toBe(0)
  })

  test('cancelling sends nothing', async () => {
    let calls = 0
    server.use(
      http.post(REVOKE, () => {
        calls += 1
        return HttpResponse.json({ user_id: 'u1', tokens_valid_after: '2026-09-03T12:00:01Z' })
      }),
    )
    signedIn()
    renderWithProviders(<SessionsSection />)

    await userEvent.click(screen.getByRole('button', { name: /Sign out everywhere/i }))
    await userEvent.click(await screen.findByRole('button', { name: /^Cancel$/i }))

    expect(calls).toBe(0)
    expect(localStorage.getItem('igni_token')).toBe('a-token')
  })

  test('confirming posts, then clears the stored token', async () => {
    let calls = 0
    server.use(
      http.post(REVOKE, () => {
        calls += 1
        return HttpResponse.json({ user_id: 'u1', tokens_valid_after: '2026-09-03T12:00:01Z' })
      }),
    )
    signedIn()
    renderWithProviders(<SessionsSection />)

    await userEvent.click(screen.getByRole('button', { name: /Sign out everywhere/i }))
    const dialog = await screen.findByRole('alertdialog')
    await userEvent.click(
      await within(dialog).findByRole('button', { name: /Sign out everywhere/i }),
    )

    await vi.waitFor(() => expect(calls).toBe(1))
    // Keeping it would leave the portal holding a credential the server
    // has just started refusing — every page failing at once, with no
    // explanation on screen.
    await vi.waitFor(() => expect(localStorage.getItem('igni_token')).toBeNull())
  })

  test('a failed revocation keeps the session and says so', async () => {
    server.use(http.post(REVOKE, () => new HttpResponse(null, { status: 500 })))
    signedIn()
    renderWithProviders(<SessionsSection />)

    await userEvent.click(screen.getByRole('button', { name: /Sign out everywhere/i }))
    const dialog = await screen.findByRole('alertdialog')
    await userEvent.click(
      await within(dialog).findByRole('button', { name: /Sign out everywhere/i }),
    )

    expect(await screen.findByText(/Could not withdraw your sessions/i)).toBeInTheDocument()
    expect(localStorage.getItem('igni_token')).toBe('a-token')
  })
})
