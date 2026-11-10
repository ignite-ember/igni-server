import { describe, expect, it, vi } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { createMemoryRouter, RouterProvider } from 'react-router-dom'
import { ThemeProvider } from '@primer/react'
import { CompliancePage } from '../CompliancePage'
import client from '../../api/client'

/**
 * F105. Four admin tools that existed only as endpoints.
 *
 * The tests worth having here are not "does the table render" — they are
 * the three places where a plausible-looking page would be wrong:
 *
 * - the audit trail is keyset-paginated, so "Older" has to send the
 *   cursor the server handed back and a filter change has to discard it;
 * - erasure edits stored rows and cannot be undone, so the button must
 *   stay disabled until the address is typed twice;
 * - a report of nothing and a report that failed must not look alike.
 */

const EMPTY_AUDIT = { events: [], next_before: null }

function renderAt(hash = '') {
  const router = createMemoryRouter([{ path: '/compliance', element: <CompliancePage /> }], {
    initialEntries: [`/compliance${hash}`],
  })
  render(
    <ThemeProvider>
      <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
        <RouterProvider router={router} />
      </QueryClientProvider>
    </ThemeProvider>,
  )
  return router
}

describe('CompliancePage tabs', () => {
  it('opens the audit trail by default', async () => {
    vi.spyOn(client, 'get').mockResolvedValue({ data: EMPTY_AUDIT } as never)

    renderAt()

    await waitFor(() => expect(client.get).toHaveBeenCalledWith(expect.stringContaining('/portal/audit-events')))
  })

  it('opens the tab named in the hash, so a runbook link lands', async () => {
    vi.spyOn(client, 'get').mockResolvedValue({ data: EMPTY_AUDIT } as never)

    renderAt('#erasure')

    // The erasure tab asks for the address twice and issues no request.
    await waitFor(() => expect(screen.getByText(/cannot be undone/i)).toBeTruthy())
  })

  it('falls back to the audit trail on a hash it does not know', async () => {
    vi.spyOn(client, 'get').mockResolvedValue({ data: EMPTY_AUDIT } as never)

    renderAt('#nonsense')

    await waitFor(() => expect(client.get).toHaveBeenCalledWith(expect.stringContaining('/portal/audit-events')))
  })
})

describe('the audit trail', () => {
  it('says so when there is nothing rather than showing an empty table', async () => {
    vi.spyOn(client, 'get').mockResolvedValue({ data: EMPTY_AUDIT } as never)

    renderAt('#audit')

    await waitFor(() => expect(screen.getByText(/Nothing matches/i)).toBeTruthy())
  })

  it('sends the cursor the server handed back when paging older', async () => {
    // Keyset pagination: the server returns the next cursor and has no
    // notion of a page number. Sending anything else silently repeats
    // the same page.
    const get = vi.spyOn(client, 'get').mockResolvedValue({
      data: {
        events: [
          {
            id: 'e1',
            occurred_at: '2026-09-01T10:00:00Z',
            actor_email: 'admin@example.com',
            actor_user_id: 'u1',
            method: 'POST',
            route: '/v1/portal/groups',
            target_type: 'group',
            target_id: 'g1',
            status_code: 200,
            outcome: 'success',
            client_host: '10.0.0.1',
            detail: null,
          },
        ],
        next_before: '2026-09-01T10:00:00Z',
      },
    } as never)

    renderAt('#audit')
    await waitFor(() => expect(screen.getByText('admin@example.com')).toBeTruthy())

    await userEvent.click(screen.getByRole('button', { name: 'Older' }))

    await waitFor(() =>
      expect(get).toHaveBeenCalledWith(expect.stringContaining('before=2026-09-01T10%3A00%3A00Z')),
    )
  })

  it('filters on the target id when the id is clicked', async () => {
    // The filter is an exact match on `target_id`, so the cell shows the
    // id alone, as a button that applies it.
    const get = vi.spyOn(client, 'get').mockResolvedValue({
      data: {
        events: [
          {
            id: 'e1',
            occurred_at: '2026-09-01T10:00:00Z',
            actor_email: 'admin@example.com',
            actor_user_id: 'u1',
            method: 'DELETE',
            route: '/v1/nodes/{node_id}',
            target_type: 'inference_node',
            target_id: 'gpu-secure-03',
            status_code: 200,
            outcome: 'success',
            client_host: '10.0.0.1',
            detail: null,
          },
        ],
        next_before: null,
      },
    } as never)

    renderAt('#audit')
    await waitFor(() => expect(screen.getByText('admin@example.com')).toBeTruthy())

    await userEvent.click(screen.getByRole('button', { name: 'gpu-secure-03' }))

    // The id alone, not the type and the id together.
    await waitFor(() => expect(get).toHaveBeenCalledWith(expect.stringContaining('target_id=gpu-secure-03')))
    const asked = get.mock.calls.map(([url]) => String(url))
    expect(asked.some((url) => url.includes('inference_node'))).toBe(false)
    // And the type is not shown at all: the route column beside it
    // already says what kind of thing this was.
    expect(screen.queryByText('inference_node')).toBeNull()
  })

  it('says a route acted on nothing rather than leaving the cell blank', async () => {
    // Most routes have no single identified target: a sign-in attempt,
    // or creating something that has no id yet. An empty cell reads as a
    // recording failure.
    vi.spyOn(client, 'get').mockResolvedValue({
      data: {
        events: [
          {
            id: 'e1',
            occurred_at: '2026-09-01T10:00:00Z',
            actor_email: null,
            actor_user_id: null,
            method: 'POST',
            route: '/v1/sign-in',
            target_type: null,
            target_id: null,
            status_code: 401,
            outcome: 'denied',
            client_host: '10.0.0.1',
            detail: null,
          },
        ],
        next_before: null,
      },
    } as never)

    renderAt('#audit')

    await waitFor(() => expect(screen.getByText('anonymous')).toBeTruthy())
    expect(screen.getByText('—')).toBeTruthy()
  })

  it("shows the server's reason when the trail cannot be read", async () => {
    vi.spyOn(client, 'get').mockRejectedValue({
      response: { status: 403, data: { detail: 'Only an admin can read the audit trail.' } },
    })

    renderAt('#audit')

    await waitFor(() => expect(screen.getByText('Only an admin can read the audit trail.')).toBeTruthy())
  })

  it('cannot page older when the server offered no cursor', async () => {
    vi.spyOn(client, 'get').mockResolvedValue({ data: EMPTY_AUDIT } as never)

    renderAt('#audit')

    await waitFor(() => expect(screen.getByRole('button', { name: 'Older' })).toBeDisabled())
  })

  it('discards the cursor when a filter changes', async () => {
    // A `before` from one filtered view means nothing in another: kept,
    // it would start the new filter part-way down.
    const get = vi.spyOn(client, 'get').mockResolvedValue({
      data: { events: [], next_before: '2026-09-01T10:00:00Z' },
    } as never)

    renderAt('#audit')
    // Wait for the button, not the request: the request starts while the
    // spinner is still up.
    await waitFor(() => expect(screen.getByRole('button', { name: 'Older' })).toBeEnabled())

    await userEvent.click(screen.getByRole('button', { name: 'Older' }))
    await waitFor(() => expect(get).toHaveBeenCalledWith(expect.stringContaining('before=')))

    get.mockClear()
    await userEvent.selectOptions(screen.getByLabelText('Outcome'), 'denied')

    await waitFor(() => expect(get).toHaveBeenCalledWith(expect.stringContaining('outcome=denied')))
    const withCursor = get.mock.calls.filter(([url]) => String(url).includes('before='))
    expect(withCursor).toHaveLength(0)
  })
})

describe('erasure', () => {
  it('refuses to run until the address is typed twice', async () => {
    vi.spyOn(client, 'get').mockResolvedValue({ data: EMPTY_AUDIT } as never)
    const post = vi.spyOn(client, 'post')

    renderAt('#erasure')

    const button = await screen.findByRole('button', { name: /Redact/i })
    expect(button).toBeDisabled()

    await userEvent.type(screen.getByLabelText('Email address to redact'), 'jane@example.com')
    // One field filled is not enough — this is the whole guard.
    expect(await screen.findByRole('button', { name: /Redact/i })).toBeDisabled()

    await userEvent.type(screen.getByLabelText('Type it again to confirm'), 'jane@example.com')
    await waitFor(() => expect(screen.getByRole('button', { name: /Redact/i })).toBeEnabled())

    expect(post).not.toHaveBeenCalled()
  })

  it('stays disabled when the two addresses differ by one character', async () => {
    vi.spyOn(client, 'get').mockResolvedValue({ data: EMPTY_AUDIT } as never)

    renderAt('#erasure')

    await userEvent.type(screen.getByLabelText('Email address to redact'), 'jane@example.com')
    await userEvent.type(screen.getByLabelText('Type it again to confirm'), 'jane@example.co')

    expect(screen.getByRole('button', { name: /Redact/i })).toBeDisabled()
    expect(screen.getByText(/do not match yet/i)).toBeTruthy()
  })

  it('distinguishes "redacted nothing" from "did not run"', async () => {
    // Both are a green-ish outcome to a naive implementation, and they
    // mean opposite things: one is a spelling mistake, the other is a
    // request that was already satisfied.
    vi.spyOn(client, 'get').mockResolvedValue({ data: EMPTY_AUDIT } as never)
    vi.spyOn(client, 'post').mockResolvedValue({
      data: { email: 'jane@example.com', events_redacted: 0, logins_included: 0 },
    } as never)

    renderAt('#erasure')

    await userEvent.type(screen.getByLabelText('Email address to redact'), 'jane@example.com')
    await userEvent.type(screen.getByLabelText('Type it again to confirm'), 'jane@example.com')
    await userEvent.click(screen.getByRole('button', { name: /Redact/i }))

    await waitFor(() => expect(screen.getByText('Nothing to redact')).toBeTruthy())
    expect(screen.getByText(/Check the spelling/i)).toBeTruthy()
  })

  it("shows the server's reason when the redaction is refused", async () => {
    vi.spyOn(client, 'get').mockResolvedValue({ data: EMPTY_AUDIT } as never)
    vi.spyOn(client, 'post').mockRejectedValue({
      response: { status: 422, data: { detail: 'That is not an email address.' } },
    })

    renderAt('#erasure')

    await userEvent.type(screen.getByLabelText('Email address to redact'), 'jane@example.com')
    await userEvent.type(screen.getByLabelText('Type it again to confirm'), 'jane@example.com')
    await userEvent.click(screen.getByRole('button', { name: /Redact/i }))

    await waitFor(() => expect(screen.getByText('The redaction did not run')).toBeTruthy())
    expect(screen.getByText('That is not an email address.')).toBeTruthy()
  })
})

describe('subject access', () => {
  it('will not submit an empty address', async () => {
    vi.spyOn(client, 'get').mockResolvedValue({ data: EMPTY_AUDIT } as never)

    renderAt('#subject-access')

    expect(await screen.findByRole('button', { name: /Produce report/i })).toBeDisabled()
  })

  it('shows the whole report rather than a summary of it', async () => {
    // A subject access response is a legal artefact. Rendering only the
    // parts the portal knows about would quietly drop whatever the
    // schema grows next.
    vi.spyOn(client, 'get').mockResolvedValue({ data: EMPTY_AUDIT } as never)
    vi.spyOn(client, 'post').mockResolvedValue({
      data: {
        subject: 'jane@example.com',
        processing: { controller: 'The organisation operating this igni deployment' },
        withheld: ['users.hashed_password'],
        sections: { account: [{ email: 'jane@example.com' }] },
      },
    } as never)

    renderAt('#subject-access')

    await userEvent.type(screen.getByLabelText('Email address'), 'jane@example.com')
    await userEvent.click(screen.getByRole('button', { name: /Produce report/i }))

    const report = await screen.findByLabelText('Subject access report')
    const text = (report as HTMLTextAreaElement).value
    expect(text).toContain('users.hashed_password')
    expect(text).toContain('controller')
    expect(text).toContain('jane@example.com')
  })
})
