/**
 * EditEntryPage tests — covers breadcrumbs + cancel navigation +
 * the absence-of-entry error state.
 */

import { afterEach, describe, expect, test } from 'vitest'
import { http, HttpResponse } from 'msw'
import userEvent from '@testing-library/user-event'
import { screen, cleanup, waitFor, within } from '@testing-library/react'
import { Routes, Route } from 'react-router-dom'
import { renderWithProviders } from '../../test/render'
import { server } from '../../test/server'
import { EditEntryPage } from '../EditEntryPage'

afterEach(() => {
  cleanup()
  server.resetHandlers()
})

const ENTRY_RESPONSE = {
  id: 'or-1',
  group_id: 'g-1',
  kind: 'plugins',
  entry_name: 'code-reviewer',
  content: 'name: code-reviewer\n',
  content_type: 'yaml',
  enabled: true,
  source_url: null,
  source_ref: null,
  source_subdir: null,
  created_by_user_id: 'demo-user-001',
  created_at: '2026-01-10T00:00:00Z',
  updated_at: '2026-01-10T00:00:00Z',
}

describe('EditEntryPage', () => {
  test('breadcrumbs show Groups > Entries > entry name', async () => {
    server.use(
      http.get('/v1/portal/groups/g-1/entries', () =>
        HttpResponse.json({ entries: [ENTRY_RESPONSE], total: 1 }),
      ),
    )

    renderWithProviders(
      <Routes>
        <Route path="/groups/:id/entries/:entryId/edit" element={<EditEntryPage />} />
      </Routes>,
      { route: '/groups/g-1/entries/or-1/edit' },
    )

    // "Groups" breadcrumb + the actual entry name appear in the tree.
    expect(await screen.findByText('Groups')).toBeInTheDocument()
    // Selected breadcrumb shows the entry name.
    const selected = document.querySelector('[aria-current="page"]')
    expect(selected?.textContent).toBe('code-reviewer')
  })

  test('shows entry not found banner when entryId is bogus', async () => {
    server.use(
      http.get('/v1/portal/groups/g-1/entries', () =>
        HttpResponse.json({ entries: [], total: 0 }),
      ),
    )

    renderWithProviders(
      <Routes>
        <Route path="/groups/:id/entries/:entryId/edit" element={<EditEntryPage />} />
      </Routes>,
      { route: '/groups/g-1/entries/does-not-exist/edit' },
    )

    expect(await screen.findByText(/entry not found/i)).toBeInTheDocument()
    expect(screen.getByRole('link', { name: /back to entries/i })).toHaveAttribute(
      'href',
      '/groups/g-1?tab=entries',
    )
  })

  test('save button submits PATCH and navigates back', async () => {
    let captured: Record<string, unknown> | null = null
    server.use(
      http.get('/v1/portal/groups/g-1/entries', () =>
        HttpResponse.json({ entries: [ENTRY_RESPONSE], total: 1 }),
      ),
      http.patch(
        '/v1/portal/groups/g-1/entries/:entryId',
        async ({ request }) => {
          captured = (await request.json()) as Record<string, unknown>
          return HttpResponse.json({ entries: [ENTRY_RESPONSE], total: 1 })
        },
      ),
    )

    const user = userEvent.setup()
    renderWithProviders(
      <Routes>
        <Route path="/groups/:id/entries/:entryId/edit" element={<EditEntryPage />} />
        <Route
          path="/groups/:id"
          element={<div data-testid="landed">landed</div>}
        />
      </Routes>,
      { route: '/groups/g-1/entries/or-1/edit' },
    )

    await screen.findByRole('heading', { name: /code-reviewer/i })
    const save = await screen.findByRole('button', { name: /save entry/i })
    await user.click(save)

    await waitFor(() => expect(captured).not.toBeNull())
    expect(captured!.content).toBe('name: code-reviewer\n')
    expect(captured!.enabled).toBe(true)

    // After successful save the form should navigate back to the
    // group details — captured by the sentinel route mounted above.
    expect(await screen.findByTestId('landed')).toBeInTheDocument()
  })

  test('cancel button navigates back without saving', async () => {
    let patched = false
    server.use(
      http.get('/v1/portal/groups/g-1/entries', () =>
        HttpResponse.json({ entries: [ENTRY_RESPONSE], total: 1 }),
      ),
      http.patch(
        '/v1/portal/groups/g-1/entries/:entryId',
        () => {
          patched = true
          return HttpResponse.json({})
        },
      ),
    )

    const user = userEvent.setup()
    renderWithProviders(
      <Routes>
        <Route path="/groups/:id/entries/:entryId/edit" element={<EditEntryPage />} />
        <Route
          path="/groups/:id"
          element={<div data-testid="landed">landed</div>}
        />
      </Routes>,
      { route: '/groups/g-1/entries/or-1/edit' },
    )

    const cancel = await screen.findByRole('link', { name: /cancel/i })
    await user.click(cancel)
    expect(await screen.findByTestId('landed')).toBeInTheDocument()
    expect(patched).toBe(false)
  })

  test('successful save navigates back to /groups/:id (banner torn down by nav)', async () => {
    // The success banner is set just before navigation runs, so
    // it's typically torn down before a test can query it. This
    // test verifies the *observable* outcome of a successful save:
    // the form unmounts (we land on the sentinel route).
    server.use(
      http.get('/v1/portal/groups/g-1/entries', () =>
        HttpResponse.json({ entries: [ENTRY_RESPONSE], total: 1 }),
      ),
      http.patch(
        '/v1/portal/groups/g-1/entries/:entryId',
        () =>
          HttpResponse.json({
            entries: [ENTRY_RESPONSE],
            total: 1,
          }),
      ),
    )

    const user = userEvent.setup()
    renderWithProviders(
      <Routes>
        <Route
          path="/groups/:id/entries/:entryId/edit"
          element={<EditEntryPage />}
        />
        <Route
          path="/groups/:id"
          element={<div data-testid="landed">landed</div>}
        />
      </Routes>,
      { route: '/groups/g-1/entries/or-1/edit' },
    )

    await screen.findByRole('heading', { name: /code-reviewer/i })
    await user.click(screen.getByRole('button', { name: /save entry/i }))
    expect(await screen.findByTestId('landed')).toBeInTheDocument()
  })
})

describe('saving and deleting', () => {
  function renderEditor(handlers: Parameters<typeof server.use>) {
    server.use(
      http.get('/v1/portal/groups/g-1/entries', () =>
        HttpResponse.json({ entries: [ENTRY_RESPONSE], total: 1 }),
      ),
      ...handlers,
    )
    renderWithProviders(
      <Routes>
        <Route path="/groups/:id/entries/:entryId/edit" element={<EditEntryPage />} />
        <Route path="/groups/:id" element={<div data-testid="landed">landed</div>} />
      </Routes>,
      { route: '/groups/g-1/entries/or-1/edit' },
    )
  }

  test('the name cannot be edited, since the API cannot rename an entry', async () => {
    renderEditor([])

    const name = (await screen.findByLabelText(/entry name/i)) as HTMLInputElement
    expect(name.readOnly).toBe(true)
    expect(screen.getByText(/name cannot be changed/i)).toBeInTheDocument()
  })

  test('warnings from the server keep the page open', async () => {
    renderEditor([
      http.patch('/v1/portal/groups/g-1/entries/:entryId', () =>
        HttpResponse.json({ entries: [ENTRY_RESPONSE], total: 1, warnings: ['Unknown tool: Raed'] }),
      ),
    ])

    const user = userEvent.setup()
    await user.click(await screen.findByRole('button', { name: /save entry/i }))

    expect(await screen.findByText('Unknown tool: Raed')).toBeInTheDocument()
    expect(screen.queryByTestId('landed')).not.toBeInTheDocument()
  })

  test('a refused save shows the server\'s reason', async () => {
    renderEditor([
      http.patch('/v1/portal/groups/g-1/entries/:entryId', () =>
        HttpResponse.json({ detail: 'That model is not served here.' }, { status: 400 }),
      ),
    ])

    const user = userEvent.setup()
    await user.click(await screen.findByRole('button', { name: /save entry/i }))

    expect(await screen.findByText('That model is not served here.')).toBeInTheDocument()
  })

  test('delete asks first, then deletes and goes back', async () => {
    let deleted = false
    renderEditor([
      http.delete('/v1/portal/groups/g-1/entries/or-1', () => {
        deleted = true
        return new HttpResponse(null, { status: 204 })
      }),
    ])

    const user = userEvent.setup()
    await user.click(await screen.findByRole('button', { name: /^delete$/i }))
    expect(deleted).toBe(false)

    const dialog = await screen.findByRole('alertdialog')
    await user.click(within(dialog).getByRole('button', { name: /^delete$/i }))

    expect(await screen.findByTestId('landed')).toBeInTheDocument()
    expect(deleted).toBe(true)
  })

  test('a refused delete shows the server\'s reason and stays', async () => {
    renderEditor([
      http.delete('/v1/portal/groups/g-1/entries/or-1', () =>
        HttpResponse.json({ detail: 'This group holds what igni ships.' }, { status: 409 }),
      ),
    ])

    const user = userEvent.setup()
    await user.click(await screen.findByRole('button', { name: /^delete$/i }))
    const dialog = await screen.findByRole('alertdialog')
    await user.click(within(dialog).getByRole('button', { name: /^delete$/i }))

    expect(await screen.findByText('This group holds what igni ships.')).toBeInTheDocument()
    expect(screen.queryByTestId('landed')).not.toBeInTheDocument()
  })
})

describe('an entry in the group that holds what igni ships', () => {
  /**
   * Reading one is the point — you look at the shipped reviewer before
   * deciding to copy the group. Saving it is not: the server refuses,
   * and a Save button that always fails is worse than no Save button.
   */
  function renderFor(isDefault: boolean) {
    server.use(
      http.get('/v1/portal/groups/g-1/entries', () =>
        HttpResponse.json({ entries: [ENTRY_RESPONSE], total: 1 }),
      ),
      http.get('/v1/portal/groups/g-1', () =>
        HttpResponse.json({
          id: 'g-1',
          org_id: 'demo-org-001',
          name: isDefault ? 'Default' : 'Legal',
          description: null,
          default_model: null,
          is_default: isDefault,
          created_by_user_id: 'u1',
          created_at: '2026-01-01T00:00:00Z',
          updated_at: '2026-01-01T00:00:00Z',
          member_count: 0,
          entry_count: 1,
        }),
      ),
    )
    renderWithProviders(
      <Routes>
        <Route path="/groups/:id/entries/:entryId/edit" element={<EditEntryPage />} />
      </Routes>,
      { route: '/groups/g-1/entries/or-1/edit' },
    )
  }

  test('its content can still be read', async () => {
    renderFor(true)

    const name = (await screen.findByLabelText(/entry name/i)) as HTMLInputElement
    expect(name.value).toBe('code-reviewer')
    expect(name.readOnly).toBe(true)
  })

  test('it says why nothing can be changed', async () => {
    renderFor(true)

    expect(await screen.findByText(/this is what igni ships/i)).toBeInTheDocument()
  })

  test('there is no Save', async () => {
    renderFor(true)

    await screen.findByText(/this is what igni ships/i)
    expect(screen.queryByRole('button', { name: /save entry/i })).not.toBeInTheDocument()
  })

  test('there is no Delete', async () => {
    renderFor(true)

    await screen.findByText(/this is what igni ships/i)
    expect(screen.queryByRole('button', { name: /^delete$/i })).not.toBeInTheDocument()
  })

  test('an entry in an ordinary group has both', async () => {
    renderFor(false)

    expect(await screen.findByRole('button', { name: /save entry/i })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /^delete$/i })).toBeInTheDocument()
  })
})
