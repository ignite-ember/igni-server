/**
 * InviteForm — validation, the request, and what the admin is told after.
 *
 * The regression this guards is a lie rather than a crash. The form used
 * to say "Invite sent to {email}." unconditionally, including on
 * deployments with no SMTP relay, where nothing was sent and the only
 * link that would have let the admin fix it by hand was discarded from
 * the response.
 *
 * A self-hosted install with no mail server is a supported
 * configuration, so both branches have to be right: say it was sent when
 * it was, and hand over the link when it was not.
 */
import { describe, expect, test, vi } from 'vitest'
import { http, HttpResponse } from 'msw'
import { screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { renderWithProviders } from '../../test/render'
import { server } from '../../test/server'
import { InviteForm } from '../TeamPage'

const INVITE_URL = 'https://igni.acme.internal/signup?email=new%40acme.test'

function respondWith(overrides: Record<string, unknown>) {
  server.use(
    http.post('/v1/portal/team/invite', () =>
      HttpResponse.json({
        id: 'm1',
        email: 'new@acme.test',
        full_name: null,
        role: 'member',
        status: 'invited',
        total_sessions: 0,
        total_tokens: 0,
        last_active: null,
        joined_at: '2026-08-29T00:00:00Z',
        invite_url: INVITE_URL,
        emailed: true,
        ...overrides,
      }),
    ),
  )
}

async function invite() {
  const user = userEvent.setup()
  await user.type(screen.getByPlaceholderText(/colleague@company.com/i), 'new@acme.test')
  await user.click(screen.getByRole('button', { name: /send invite/i }))
  return user
}

describe('InviteForm', () => {
  test('reports success when the message actually went out', async () => {
    respondWith({ emailed: true })
    const onSuccess = vi.fn()
    renderWithProviders(<InviteForm onSuccess={onSuccess} onError={vi.fn()} />)

    await invite()

    await waitFor(() => expect(onSuccess).toHaveBeenCalledWith('Invite sent to new@acme.test.'))
  })

  // The whole point of the change.
  test('does not claim an email was sent when none was', async () => {
    respondWith({ emailed: false })
    const onSuccess = vi.fn()
    renderWithProviders(<InviteForm onSuccess={onSuccess} onError={vi.fn()} />)

    await invite()

    await waitFor(() => expect(screen.getByText(/No email was sent/i)).toBeInTheDocument())
    expect(onSuccess).not.toHaveBeenCalled()
  })

  test('hands over the claim link so the admin can pass it on', async () => {
    respondWith({ emailed: false })
    renderWithProviders(<InviteForm onSuccess={vi.fn()} onError={vi.fn()} />)

    await invite()

    await waitFor(() => expect(screen.getByDisplayValue(INVITE_URL)).toBeInTheDocument())
  })

  test('names the person who was not notified', async () => {
    respondWith({ emailed: false })
    renderWithProviders(<InviteForm onSuccess={vi.fn()} onError={vi.fn()} />)

    await invite()

    await waitFor(() => expect(screen.getByText(/new@acme.test has not been notified/i)).toBeInTheDocument())
  })

  test('says the membership exists regardless, so the invite is not retried pointlessly', async () => {
    respondWith({ emailed: false })
    renderWithProviders(<InviteForm onSuccess={vi.fn()} onError={vi.fn()} />)

    await invite()

    await waitFor(() => expect(screen.getByText(/claim the invite/i)).toBeInTheDocument())
  })

  test('a successful send shows no leftover link panel', async () => {
    respondWith({ emailed: true })
    renderWithProviders(<InviteForm onSuccess={vi.fn()} onError={vi.fn()} />)

    await invite()

    await waitFor(() => expect(screen.queryByText(/No email was sent/i)).not.toBeInTheDocument())
  })

  // ── Client-side validation ────────────────────────────────────────
  // Nothing reaches the network until the address looks like one, so a
  // typo costs a round trip and an error toast rather than a membership
  // row for a nonexistent mailbox.

  test('rejects empty email before sending', async () => {
    const onError = vi.fn()
    renderWithProviders(<InviteForm onSuccess={vi.fn()} onError={onError} />)

    await userEvent.click(screen.getByRole('button', { name: /Send invite/i }))

    expect(screen.getByText(/Email is required/i)).toBeInTheDocument()
    expect(onError).not.toHaveBeenCalled()
  })

  test('rejects malformed email before sending', async () => {
    renderWithProviders(<InviteForm onSuccess={vi.fn()} onError={vi.fn()} />)

    await userEvent.type(screen.getByPlaceholderText(/colleague@company.com/i), 'not-an-email')
    await userEvent.click(screen.getByRole('button', { name: /Send invite/i }))

    expect(screen.getByText(/valid email/i)).toBeInTheDocument()
  })

  test('sends the chosen address and role, then clears the field', async () => {
    const onSuccess = vi.fn()
    let captured: { email?: string; role?: string } = {}

    server.use(
      http.post('/v1/portal/team/invite', async ({ request }) => {
        captured = (await request.json()) as typeof captured
        return HttpResponse.json({
          id: 'm-99',
          email: captured.email,
          full_name: null,
          role: captured.role,
          status: 'invited',
          total_sessions: 0,
          total_tokens: 0,
          last_active: null,
          joined_at: '2026-05-17T00:00:00Z',
          invite_url: INVITE_URL,
          emailed: true,
        })
      }),
    )

    renderWithProviders(<InviteForm onSuccess={onSuccess} onError={vi.fn()} />)

    await userEvent.type(screen.getByPlaceholderText(/colleague/i), 'alice@example.com')
    await userEvent.click(screen.getByRole('button', { name: /Send invite/i }))

    await waitFor(() => {
      expect(onSuccess).toHaveBeenCalledWith(expect.stringContaining('alice@example.com'))
    })
    expect(captured).toEqual({ email: 'alice@example.com', role: 'member' })
    expect(screen.getByPlaceholderText(/colleague/i)).toHaveValue('')
  })

  test('surfaces a backend error via onError', async () => {
    const onError = vi.fn()
    server.use(
      http.post('/v1/portal/team/invite', () =>
        HttpResponse.json(
          { detail: 'A member with email alice@x already exists in this organization' },
          { status: 409 },
        ),
      ),
    )

    renderWithProviders(<InviteForm onSuccess={vi.fn()} onError={onError} />)

    await userEvent.type(screen.getByPlaceholderText(/colleague/i), 'alice@example.com')
    await userEvent.click(screen.getByRole('button', { name: /Send invite/i }))

    await waitFor(() => {
      expect(onError).toHaveBeenCalledWith(expect.stringContaining('already exists'))
    })
  })
})
