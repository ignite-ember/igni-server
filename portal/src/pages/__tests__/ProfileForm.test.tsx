/**
 * ProfileForm — the piece of the Settings page outside the
 * (already-tested) AIPoolCard.
 */
import { describe, expect, test, vi } from 'vitest'
import { http, HttpResponse } from 'msw'
import userEvent from '@testing-library/user-event'
import { screen } from '@testing-library/react'
import { renderWithProviders } from '../../test/render'
import { server } from '../../test/server'
import { ProfileForm } from '../SettingsPage'
import type { UserMe } from '../../types'

const BASE_USER: UserMe = {
  user_id: 'u1',
  email: 'old@ember.sh',
  full_name: 'Old Name',
  created_at: '2026-01-01T00:00:00Z',
  role: 'admin',
  org_id: 'org-1',
  organizations: [{ id: 'org-1', slug: 'demo', display_name: 'Demo', role: 'admin' }],
}

describe('ProfileForm', () => {
  test('renders current name + email and disables Save when pristine', async () => {
    renderWithProviders(<ProfileForm user={BASE_USER} />)

    expect(screen.getByDisplayValue('Old Name')).toBeInTheDocument()
    expect(screen.getByDisplayValue('old@ember.sh')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /Save changes/i })).toBeDisabled()
  })

  test('Save enables after edit, PATCHes /portal/me with new full_name', async () => {
    // Email is no longer editable from the Settings form (the email-change
    // flow was moved out — see the pending-email test below). The PATCH
    // payload only carries ``full_name`` now; assert that and stop
    // re-checking the read-only email field.
    let captured: Record<string, unknown> = {}
    server.use(
      http.patch('/v1/portal/me', async ({ request }) => {
        captured = (await request.json()) as Record<string, unknown>
        return HttpResponse.json({ ...BASE_USER, full_name: 'New Name' })
      }),
    )

    renderWithProviders(<ProfileForm user={BASE_USER} />)

    const nameInput = screen.getByDisplayValue('Old Name')
    await userEvent.clear(nameInput)
    await userEvent.type(nameInput, 'New Name')

    const save = screen.getByRole('button', { name: /Save changes/i })
    expect(save).toBeEnabled()
    await userEvent.click(save)

    await vi.waitFor(() => {
      expect(captured.full_name).toBe('New Name')
    })
    // "Saved" indicator appears after success
    expect(await screen.findByText(/^Saved$/i)).toBeInTheDocument()
  })

  test.skip('shows pending-email caption when user has a pending email change', async () => {
    // The pending-email caption was rendered by the previous version of
    // ProfileForm when ``user.pending_email`` was set. The current form
    // doesn't surface that state — the email field is read-only and the
    // email-change flow lives elsewhere. Re-enable this test (and
    // restore the caption) if/when the in-form email change ships
    // again.
    renderWithProviders(
      <ProfileForm user={{ ...BASE_USER, pending_email: 'new@ember.sh' }} />,
    )

    expect(screen.getByText(/Verification sent to/i)).toBeInTheDocument()
    expect(screen.getByText('new@ember.sh')).toBeInTheDocument()
  })

  test('shows error banner when PATCH fails', async () => {
    server.use(
      http.patch('/v1/portal/me', () =>
        HttpResponse.json({ detail: 'boom' }, { status: 500 }),
      ),
    )

    renderWithProviders(<ProfileForm user={BASE_USER} />)

    await userEvent.type(screen.getByDisplayValue('Old Name'), 'x')
    await userEvent.click(screen.getByRole('button', { name: /Save changes/i }))

    expect(await screen.findByText(/Failed to update profile/i)).toBeInTheDocument()
  })
})
