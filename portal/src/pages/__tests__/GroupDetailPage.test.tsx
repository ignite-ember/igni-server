/**
 * GroupDetailPage tests — covers group detail view with Members/Entries tabs.
 */
import { afterEach, describe, expect, test } from 'vitest'
import { http, HttpResponse } from 'msw'
import userEvent from '@testing-library/user-event'
import { screen, cleanup, waitFor, within } from '@testing-library/react'
import { renderWithProviders } from '../../test/render'
import { server } from '../../test/server'
import { GroupDetailPage } from '../GroupDetailPage'
import { Routes, Route } from 'react-router-dom'

afterEach(() => {
  cleanup()
  server.resetHandlers()
})

const GROUP_DATA = {
  id: 'g-1',
  org_id: 'demo-org-001',
  name: 'Engineering',
  description: 'The engineering team',
  created_by_user_id: 'demo-user-001',
  created_at: '2026-01-01T00:00:00Z',
  updated_at: '2026-01-15T00:00:00Z',
  member_count: 2,
  entry_count: 1,
}

const MEMBERS_DATA = {
  members: [
    {
      id: 'gm-1',
      user_id: 'u-1',
      group_id: 'g-1',
      full_name: 'Alice Smith',
      email: 'alice@ember.sh',
      added_by_user_id: 'demo-user-001',
      added_at: '2026-01-05T00:00:00Z',
    },
    {
      id: 'gm-2',
      user_id: 'u-2',
      group_id: 'g-1',
      full_name: 'Bob Jones',
      email: 'bob@ember.sh',
      added_by_user_id: 'demo-user-001',
      added_at: '2026-01-06T00:00:00Z',
    },
  ],
  total: 2,
}

const ENTRIES_DATA = {
  entries: [
    {
      id: 'or-1',
      group_id: 'g-1',
      kind: 'agents',
      entry_name: 'code-reviewer',
      content: '# Code Reviewer\nA helpful code reviewer.',
      content_type: 'markdown',
      enabled: true,
      created_by_user_id: 'demo-user-001',
      created_at: '2026-01-10T00:00:00Z',
      updated_at: '2026-01-10T00:00:00Z',
    },
  ],
  total: 1,
}

const TEAM_DATA = {
  members: [
    {
      id: 'm-1',
      user_id: 'demo-user-001',
      email: 'alex@ember.sh',
      full_name: 'Alex Chen',
      role: 'admin',
      status: 'active',
    },
    {
      id: 'm-2',
      user_id: 'u-3',
      email: 'carol@ember.sh',
      full_name: 'Carol White',
      role: 'member',
      status: 'active',
    },
  ],
}

describe('GroupDetailPage', () => {
  test('renders loading skeleton', () => {
    renderWithProviders(
      <Routes>
        <Route path="/groups/:id" element={<GroupDetailPage />} />
      </Routes>,
      { route: '/groups/g-1' },
    )
    // Level 1: the page title is the page's h1 (F131). While loading it
    // holds a skeleton rather than text, so it has no accessible name —
    // ``getByRole`` still finds it by role and level.
    expect(screen.getByRole('heading', { level: 1 })).toBeInTheDocument()
  })

  test('renders group name and tabs', async () => {
    server.use(
      http.get('/v1/portal/groups/:groupId', () => HttpResponse.json(GROUP_DATA)),
    )
    renderWithProviders(
      <Routes>
        <Route path="/groups/:id" element={<GroupDetailPage />} />
      </Routes>,
      { route: '/groups/g-1' },
    )

    expect(await screen.findByRole('heading', { name: 'Engineering' })).toBeInTheDocument()
    expect(screen.getByRole('link', { name: /members/i })).toBeInTheDocument()
    expect(screen.getByRole('link', { name: /configuration/i })).toBeInTheDocument()
  })

  test('the description lives on the General tab, not in the header', async () => {
    server.use(
      http.get('/v1/portal/groups/:groupId', () => HttpResponse.json(GROUP_DATA)),
    )
    renderWithProviders(
      <Routes>
        <Route path="/groups/:id" element={<GroupDetailPage />} />
      </Routes>,
      { route: '/groups/g-1?tab=settings' },
    )

    expect(await screen.findByRole('heading', { name: 'Engineering' })).toBeInTheDocument()
    expect(screen.getByText(/the engineering team/i)).toBeInTheDocument()
  })

  test('members tab shows member list', async () => {
    server.use(
      http.get('/v1/portal/groups/:groupId', () => HttpResponse.json(GROUP_DATA)),
      http.get('/v1/portal/groups/:groupId/members', () => HttpResponse.json(MEMBERS_DATA)),
      http.get('/v1/portal/team', () => HttpResponse.json(TEAM_DATA)),
    )
    renderWithProviders(
      <Routes>
        <Route path="/groups/:id" element={<GroupDetailPage />} />
      </Routes>,
      { route: '/groups/g-1' },
    )

    await screen.findByRole('heading', { name: 'Engineering' })
    await userEvent.click(screen.getByRole('link', { name: /members/i }))
    expect(await screen.findByText('Alice Smith')).toBeInTheDocument()
    expect(screen.getByText('Bob Jones')).toBeInTheDocument()
  })

  test('members tab shows empty state when no members', async () => {
    server.use(
      http.get('/v1/portal/groups/:groupId', () => HttpResponse.json({ ...GROUP_DATA, member_count: 0 })),
      http.get('/v1/portal/groups/:groupId/members', () => HttpResponse.json({ members: [], total: 0 })),
      http.get('/v1/portal/team', () => HttpResponse.json(TEAM_DATA)),
    )
    renderWithProviders(
      <Routes>
        <Route path="/groups/:id" element={<GroupDetailPage />} />
      </Routes>,
      { route: '/groups/g-1' },
    )

    await screen.findByRole('heading', { name: 'Engineering' })
    await userEvent.click(screen.getByRole('link', { name: /members/i }))
    expect(await screen.findByText(/no members/i)).toBeInTheDocument()
  })

  test('configuration tab heads each table with its kind', async () => {
    server.use(
      http.get('/v1/portal/groups/:groupId', () => HttpResponse.json(GROUP_DATA)),
      http.get('/v1/portal/groups/:groupId/entries', () => HttpResponse.json(ENTRIES_DATA)),
    )
    renderWithProviders(
      <Routes>
        <Route path="/groups/:id" element={<GroupDetailPage />} />
      </Routes>,
      { route: '/groups/g-1' },
    )

    await screen.findByRole('heading', { name: 'Engineering' })
    await userEvent.click(screen.getByRole('link', { name: /configuration/i }))

    // The kind is the table's heading, so "which of these are MCP
    // servers" is answered without reading a row.
    expect(await screen.findByRole('link', { name: 'code-reviewer' })).toBeInTheDocument()
    expect(screen.getByText('Agents')).toBeInTheDocument()
    // And a kind with nothing in it is not an empty table.
    expect(screen.queryByText('MCP Servers')).not.toBeInTheDocument()
  })

  test('configuration tab shows empty state when no entries', async () => {
    server.use(
      http.get('/v1/portal/groups/:groupId', () => HttpResponse.json({ ...GROUP_DATA, entry_count: 0 })),
      http.get('/v1/portal/groups/:groupId/entries', () => HttpResponse.json({ entries: [], total: 0 })),
    )
    renderWithProviders(
      <Routes>
        <Route path="/groups/:id" element={<GroupDetailPage />} />
      </Routes>,
      { route: '/groups/g-1' },
    )

    await screen.findByRole('heading', { name: 'Engineering' })
    await userEvent.click(screen.getByRole('link', { name: /configuration/i }))
    expect(await screen.findByText(/nothing configured yet/i)).toBeInTheDocument()
  })

  test('shows error banner when group load fails', async () => {
    server.use(
      http.get('/v1/portal/groups/:groupId', () => HttpResponse.error()),
    )
    renderWithProviders(
      <Routes>
        <Route path="/groups/:id" element={<GroupDetailPage />} />
      </Routes>,
      { route: '/groups/g-1' },
    )

    expect(await screen.findByText(/group not found/i)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /back to groups/i })).toBeInTheDocument()
  })

  test('non-admin overview has no edit affordances', async () => {
    server.use(
      http.get('/v1/portal/me', () =>
        HttpResponse.json({
          user_id: 'u-1',
          email: 'alice@ember.sh',
          full_name: 'Alice Smith',
          created_at: '2026-01-01T00:00:00Z',
          role: 'member',
          pending_email: null,
          auth_provider: 'email',
          org_id: 'demo-org-001',
          org_slug: 'demo',
          org_display_name: 'Demo Org',
          organizations: [
            { id: 'demo-org-001', slug: 'demo', display_name: 'Demo Org', role: 'member' },
          ],
        }),
      ),
      http.get('/v1/portal/groups/:groupId', () => HttpResponse.json(GROUP_DATA)),
    )
    renderWithProviders(
      <Routes>
        <Route path="/groups/:id" element={<GroupDetailPage />} />
      </Routes>,
      { route: '/groups/g-1' },
    )

    await screen.findByRole('heading', { name: 'Engineering' })
    expect(screen.queryByRole('button', { name: /edit/i })).not.toBeInTheDocument()
  })

  test('admin sees New entry link on the configuration tab', async () => {
    server.use(
      http.get('/v1/portal/groups/:groupId', () => HttpResponse.json(GROUP_DATA)),
      http.get('/v1/portal/groups/:groupId/entries', () => HttpResponse.json(ENTRIES_DATA)),
    )
    renderWithProviders(
      <Routes>
        <Route path="/groups/:id" element={<GroupDetailPage />} />
        <Route path="/groups/:id/entries/new" element={<div>New entry form</div>} />
      </Routes>,
      { route: '/groups/g-1' },
    )

    await screen.findByRole('heading', { name: 'Engineering' })
    await userEvent.click(screen.getByRole('link', { name: /configuration/i }))
    const link = await screen.findByRole('link', { name: /new entry/i })
    expect(link).toHaveAttribute('href', '/groups/g-1/entries/new')
  })

  test('clicking a row navigates to the edit page', async () => {
    server.use(
      http.get('/v1/portal/groups/:groupId', () => HttpResponse.json(GROUP_DATA)),
      http.get('/v1/portal/groups/:groupId/entries', () =>
        HttpResponse.json(ENTRIES_DATA),
      ),
    )

    renderWithProviders(
      <Routes>
        <Route path="/groups/:id" element={<GroupDetailPage />} />
        <Route
          path="/groups/:id/entries/:entryId/edit"
          element={<div data-testid="edit-route">edit form</div>}
        />
      </Routes>,
      { route: '/groups/g-1' },
    )

    await screen.findByRole('heading', { name: 'Engineering' })
    await userEvent.click(screen.getByRole('link', { name: /configuration/i }))

    // The card itself is now an <a href> — clicking the body
    // (anywhere outside the kebab action area) should follow the
    // link to the edit page. The kebab test above covers the
    // click navigation; here we just verify the link wiring.
    const card = await screen.findByRole('link', { name: /code-reviewer/i })
    expect(card).toHaveAttribute('href', '/groups/g-1/entries/or-1/edit')
    // The card root must be an <a>, not a <div> — verifies that
    // href rendered as the polymorphic tag, not the no-href fallback.
    expect(card.tagName).toBe('A')
  })

})

describe('Group model', () => {
  /** The picker lives on the Settings tab now — one home for the
   *  group's own configuration, rather than a control in the page
   *  header and another inside the entries list. */
  function renderSettings() {
    renderWithProviders(
      <Routes>
        <Route path="/groups/:id" element={<GroupDetailPage />} />
      </Routes>,
      { route: '/groups/g1?tab=settings' },
    )
  }

  /**
   * A legal team should reach a LoRA adapter trained for legal review
   * without anyone typing its name. The list comes from the deployment
   * because adapter names are whatever somebody passed to
   * `--lora-modules`, and typing them from memory is how a group ends up
   * pointed at a model nothing serves.
   */
  function withGroup(entries: Record<string, unknown> = {}) {
    server.use(
      http.get('/v1/portal/groups/g1', () =>
        HttpResponse.json({
          id: 'g1',
          org_id: 'demo-org-001',
          name: 'Legal',
          description: null,
          default_model: null,
          created_by_user_id: 'u1',
          created_at: '2026-01-01T00:00:00Z',
          updated_at: '2026-01-01T00:00:00Z',
          member_count: 0,
          entry_count: 0,
          ...entries,
        }),
      ),
      // The portal endpoint. /chat/models authenticates a different
      // scheme and answers 401, which the client turns into a logout.
      http.get('/v1/portal/inference/models', () =>
        HttpResponse.json({ models: ['igni-1.0-27b', 'legal-reviewer'] }),
      ),
    )
  }

  test('never calls the client endpoint, which would log the admin out', async () => {
    // /chat/models authenticates a real user JWT. The portal session is
    // a different scheme, so it answers 401 — and the API client treats
    // any 401 as an expired session and clears the token. Opening this
    // page used to do exactly that.
    localStorage.setItem('igni_token', 'a-valid-looking-token')
    let calledClientEndpoint = false
    withGroup()
    server.use(
      http.get('/v1/chat/models', () => {
        calledClientEndpoint = true
        return new HttpResponse(null, { status: 401 })
      }),
    )

    renderSettings()

    await screen.findByRole('button', { name: /Deployment default/i })
    expect(calledClientEndpoint).toBe(false)
    expect(localStorage.getItem('igni_token')).not.toBeNull()
  })

  test('shows the deployment default when nothing is pinned', async () => {
    withGroup()
    renderSettings()

    expect(await screen.findByRole('button', { name: /Deployment default/i })).toBeInTheDocument()
  })

  test('shows the pinned adapter when there is one', async () => {
    withGroup({ default_model: 'legal-reviewer' })
    renderSettings()

    expect(await screen.findByRole('button', { name: /legal-reviewer/i })).toBeInTheDocument()
  })

  test('picking one sends it to the group', async () => {
    let patched: unknown = null
    withGroup()
    server.use(
      http.patch('/v1/portal/groups/g1', async ({ request }) => {
        patched = await request.json()
        return HttpResponse.json({})
      }),
    )

    renderSettings()
    const user = userEvent.setup()
    await user.click(await screen.findByRole('button', { name: /Deployment default/i }))
    await user.click(await screen.findByRole('menuitemradio', { name: /legal-reviewer/i }))

    await waitFor(() => expect(patched).toEqual({ default_model: 'legal-reviewer' }))
  })

  test('clearing it sends an empty string, not null', async () => {
    // Null already means "not supplied" on a partial update, so it
    // cannot also mean "clear".
    let patched: unknown = null
    withGroup({ default_model: 'legal-reviewer' })
    server.use(
      http.patch('/v1/portal/groups/g1', async ({ request }) => {
        patched = await request.json()
        return HttpResponse.json({})
      }),
    )

    renderSettings()
    const user = userEvent.setup()
    await user.click(await screen.findByRole('button', { name: /legal-reviewer/i }))
    await user.click(await screen.findByRole('menuitemradio', { name: /Deployment default/i }))

    await waitFor(() => expect(patched).toEqual({ default_model: '' }))
  })
})


describe('the group holding what igni ships', () => {
  /**
   * It is read-only: the copy of the distribution, and the group every
   * other one starts from. An org that edited it would have no
   * reference point left and nothing to copy. The server refuses the
   * writes; these are about not offering them in the first place.
   */
  function withDefault(isDefault: boolean) {
    server.use(
      http.get('/v1/portal/groups/g1', () =>
        HttpResponse.json({
          id: 'g1',
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
      http.get('/v1/portal/inference/models', () => HttpResponse.json({ models: [] })),
      http.get('/v1/portal/groups/g1/entries', () => HttpResponse.json(ENTRIES_DATA)),
    )
    renderWithProviders(
      <Routes>
        <Route path="/groups/:id" element={<GroupDetailPage />} />
      </Routes>,
      { route: '/groups/g1?tab=entries' },
    )
  }

  test('says what it is', async () => {
    withDefault(true)

    expect(await screen.findByText(/this is what igni ships/i)).toBeInTheDocument()
  })

  test('offers no way to add to it', async () => {
    withDefault(true)

    await screen.findByText(/this is what igni ships/i)
    expect(screen.queryByRole('link', { name: /new entry/i })).not.toBeInTheDocument()
  })

  test('an ordinary group offers a way to add one', async () => {
    withDefault(false)

    expect(await screen.findByRole('link', { name: /new entry/i })).toBeInTheDocument()
  })

  test('its entries still open, because reading them is the point', async () => {
    withDefault(true)

    expect(await screen.findByRole('link', { name: /code-reviewer/i })).toBeInTheDocument()
  })
})

describe('deleting an entry', () => {
  /**
   * It takes something away from everyone in the group on their next
   * sync, so it asks first — the same shape the Members tab uses for
   * removing somebody.
   */
  function renderConfiguration() {
    server.use(
      http.get('/v1/portal/groups/:groupId', () => HttpResponse.json(GROUP_DATA)),
      http.get('/v1/portal/groups/:groupId/entries', () => HttpResponse.json(ENTRIES_DATA)),
    )
    renderWithProviders(
      <Routes>
        <Route path="/groups/:id" element={<GroupDetailPage />} />
      </Routes>,
      { route: '/groups/g-1?tab=entries' },
    )
  }

  test('asks before it deletes', async () => {
    let deleted = false
    renderConfiguration()
    server.use(
      http.delete('/v1/portal/groups/:groupId/entries/:entryId', () => {
        deleted = true
        return new HttpResponse(null, { status: 204 })
      }),
    )

    const user = userEvent.setup()
    await user.click(await screen.findByRole('button', { name: /actions for code-reviewer/i }))
    await user.click(await screen.findByRole('menuitem', { name: /delete/i }))

    // The dialog is up and nothing has happened yet.
    expect(await screen.findByRole('alertdialog')).toBeInTheDocument()
    expect(deleted).toBe(false)
  })

  test('cancelling leaves it alone', async () => {
    let deleted = false
    renderConfiguration()
    server.use(
      http.delete('/v1/portal/groups/:groupId/entries/:entryId', () => {
        deleted = true
        return new HttpResponse(null, { status: 204 })
      }),
    )

    const user = userEvent.setup()
    await user.click(await screen.findByRole('button', { name: /actions for code-reviewer/i }))
    await user.click(await screen.findByRole('menuitem', { name: /delete/i }))
    await user.click(await screen.findByRole('button', { name: /cancel/i }))

    await waitFor(() => expect(screen.queryByRole('alertdialog')).not.toBeInTheDocument())
    expect(deleted).toBe(false)
  })

  test('confirming sends the delete', async () => {
    let deleted = false
    renderConfiguration()
    server.use(
      http.delete('/v1/portal/groups/:groupId/entries/:entryId', () => {
        deleted = true
        return new HttpResponse(null, { status: 204 })
      }),
    )

    const user = userEvent.setup()
    await user.click(await screen.findByRole('button', { name: /actions for code-reviewer/i }))
    await user.click(await screen.findByRole('menuitem', { name: /delete/i }))
    const dialog = await screen.findByRole('alertdialog')
    await user.click(within(dialog).getByRole('button', { name: /delete/i }))

    await waitFor(() => expect(deleted).toBe(true))
  })
})

describe('the General tab', () => {
  /**
   * It leads, because "what is this group" is the question somebody
   * arrives with. Members and Configuration answer follow-ups.
   */
  function renderGroup(route = '/groups/g-1') {
    server.use(
      http.get('/v1/portal/groups/:groupId', () => HttpResponse.json(GROUP_DATA)),
      http.get('/v1/portal/groups/:groupId/entries', () => HttpResponse.json(ENTRIES_DATA)),
      http.get('/v1/portal/inference/models', () => HttpResponse.json({ models: [] })),
    )
    renderWithProviders(
      <Routes>
        <Route path="/groups/:id" element={<GroupDetailPage />} />
      </Routes>,
      { route },
    )
  }

  test('is what you land on', async () => {
    renderGroup()

    // Its description card, rather than the members table.
    expect(await screen.findByText(/what this group is for/i)).toBeInTheDocument()
  })

  /** The stat card with this label — "Members" is also a tab. */
  function statCard(label: string): HTMLElement | undefined {
    return Array.from(document.querySelectorAll<HTMLElement>('.stat-card')).find((card) =>
      card.textContent?.startsWith(label),
    )
  }

  test('counts the people in the group', async () => {
    renderGroup()
    await screen.findByText(/what this group is for/i)

    expect(within(statCard('Members')!).getByText('2')).toBeInTheDocument()
  })

  test('counts what the group ships, by kind', async () => {
    renderGroup()

    // The counts arrive with the entries, after the card shell.
    await screen.findByText('Agents')
    expect(within(statCard('Agents')!).getByText('1')).toBeInTheDocument()
  })

  test('says nothing about kinds the group does not ship', async () => {
    // A wall of zeroes is not an overview.
    renderGroup()
    await screen.findByText(/what this group is for/i)

    expect(statCard('Workflows')).toBeUndefined()
  })

  test('editing the description opens a page, not a dialog', async () => {
    renderGroup()

    const edit = await screen.findByRole('link', { name: /edit/i })
    expect(edit).toHaveAttribute('href', '/groups/g-1/edit')
  })
})

describe('the tab counts', () => {
  /**
   * Primer reserves the width a tab takes when selected and bold, via
   * `content: attr(data-content)`. Passing children as an array made
   * that attribute the string "Members ,[object Object]" — which it
   * then measured, leaving two tabs a hundred pixels wider than their
   * text. `counter` is the prop for this.
   */
  test('the count is a counter, not a label stuffed into the children', async () => {
    server.use(
      http.get('/v1/portal/groups/:groupId', () => HttpResponse.json(GROUP_DATA)),
      http.get('/v1/portal/inference/models', () => HttpResponse.json({ models: [] })),
      http.get('/v1/portal/groups/:groupId/entries', () => HttpResponse.json(ENTRIES_DATA)),
    )
    renderWithProviders(
      <Routes>
        <Route path="/groups/:id" element={<GroupDetailPage />} />
      </Routes>,
      { route: '/groups/g-1' },
    )

    const tab = await screen.findByRole('link', { name: /^Members/ })
    const text = tab.querySelector('[data-component="text"]')
    expect(text?.getAttribute('data-content')).toBe('Members')
  })
})

describe('GroupDetailPage extensions tab', () => {
  const EXTENSIONS = {
    extensions: [
      {
        extension_id: 'igni.codeindex',
        name: 'CodeIndex',
        description: 'Semantic code intelligence.',
        service_url: 'https://codeindex.example.com',
        enabled: false,
      },
    ],
  }

  test('an approved extension is off until the admin switches it on', async () => {
    const switched: Array<{ id: string; enabled: boolean }> = []
    server.use(
      http.get('/v1/portal/groups/:groupId', () => HttpResponse.json(GROUP_DATA)),
      http.get('/v1/portal/groups/:groupId/extensions', () => HttpResponse.json(EXTENSIONS)),
      http.put('/v1/portal/groups/:groupId/extensions/:extensionId', async ({ params, request }) => {
        const body = (await request.json()) as { enabled: boolean }
        switched.push({ id: String(params.extensionId), enabled: body.enabled })
        return new HttpResponse(null, { status: 204 })
      }),
    )
    renderWithProviders(
      <Routes>
        <Route path="/groups/:id" element={<GroupDetailPage />} />
      </Routes>,
      { route: '/groups/g-1?tab=extensions' },
    )

    expect(await screen.findByText('CodeIndex')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'https://codeindex.example.com' })).toBeInTheDocument()
    const toggle = screen.getByRole('button', { name: 'CodeIndex' })
    expect(toggle).toHaveAttribute('aria-pressed', 'false')

    await userEvent.click(toggle)

    await waitFor(() => expect(switched).toEqual([{ id: 'igni.codeindex', enabled: true }]))
  })

  test('with nothing approved it says where extensions come from', async () => {
    server.use(
      http.get('/v1/portal/groups/:groupId', () => HttpResponse.json(GROUP_DATA)),
      http.get('/v1/portal/groups/:groupId/extensions', () => HttpResponse.json({ extensions: [] })),
    )
    renderWithProviders(
      <Routes>
        <Route path="/groups/:id" element={<GroupDetailPage />} />
      </Routes>,
      { route: '/groups/g-1?tab=extensions' },
    )

    expect(await screen.findByText(/once a super admin approves the service/i)).toBeInTheDocument()
  })
})

describe('moving people between groups', () => {
  /**
   * Everybody is in exactly one group, so the members tab moves people
   * rather than adding them: it shows the group each person is in now,
   * and taking someone out of a group puts them back in Default.
   */
  const GROUPS = {
    groups: [
      { ...GROUP_DATA, is_default: false },
      { ...GROUP_DATA, id: 'g-default', name: 'Default', is_default: true },
      { ...GROUP_DATA, id: 'g-legal', name: 'Legal', is_default: false },
    ],
    total: 3,
  }

  function render(isDefault = false) {
    server.use(
      http.get('/v1/portal/groups/:groupId', () => HttpResponse.json({ ...GROUP_DATA, is_default: isDefault })),
      http.get('/v1/portal/groups/:groupId/members', () => HttpResponse.json(MEMBERS_DATA)),
      http.get('/v1/portal/team', () => HttpResponse.json(TEAM_DATA)),
      http.get('/v1/portal/groups', () => HttpResponse.json(GROUPS)),
      http.get('/v1/portal/group-memberships', () =>
        HttpResponse.json({
          memberships: [
            { user_id: 'u-1', group_id: 'g-1', group_name: 'Engineering', is_default: false },
            { user_id: 'u-2', group_id: 'g-1', group_name: 'Engineering', is_default: false },
            { user_id: 'm-2', group_id: 'g-default', group_name: 'Default', is_default: true },
          ],
        }),
      ),
    )
    renderWithProviders(
      <Routes>
        <Route path="/groups/:id" element={<GroupDetailPage />} />
      </Routes>,
      { route: '/groups/g-1?tab=members' },
    )
  }

  test('a member can be moved to another group', async () => {
    let posted: { group: string; body: unknown } | null = null
    server.use(
      http.post('/v1/portal/groups/:groupId/members', async ({ params, request }) => {
        posted = { group: String(params.groupId), body: await request.json() }
        return HttpResponse.json({ members: [], total: 0 })
      }),
    )
    render()

    await screen.findByText('Alice Smith')
    const [moveAlice] = await screen.findAllByRole('button', { name: /move to/i })
    await userEvent.click(moveAlice)
    await userEvent.click(await screen.findByRole('menuitem', { name: 'Legal' }))

    await waitFor(() => expect(posted).toEqual({ group: 'g-legal', body: { user_id: 'u-1' } }))
  })

  test('the picker says which group each person is in now', async () => {
    render()

    await screen.findByText('Alice Smith')
    await userEvent.click(await screen.findByRole('button', { name: /move someone here/i }))

    expect(await screen.findByText(/currently in default/i)).toBeInTheDocument()
  })

  test('removing says they go back to Default', async () => {
    render()

    await screen.findByText('Alice Smith')
    const [removeAlice] = screen.getAllByRole('button', { name: /remove/i })
    await userEvent.click(removeAlice)

    expect(await screen.findByText(/go back to the default group/i)).toBeInTheDocument()
  })

  test('nobody is removed from the Default group, only moved', async () => {
    render(true)

    await screen.findByText('Alice Smith')
    expect(screen.queryByRole('button', { name: /remove/i })).not.toBeInTheDocument()
    expect(screen.getAllByRole('button', { name: /move to/i }).length).toBe(2)
  })
})

describe('copying a group', () => {
  test('copies it under a new name and opens the copy', async () => {
    let body: unknown = null
    server.use(
      http.get('/v1/portal/groups/:groupId', () => HttpResponse.json(GROUP_DATA)),
      http.post('/v1/portal/groups/g-1/copy', async ({ request }) => {
        body = await request.json()
        return HttpResponse.json({ ...GROUP_DATA, id: 'g-copy', name: 'Platform' }, { status: 201 })
      }),
    )
    renderWithProviders(
      <Routes>
        <Route path="/groups/:id" element={<GroupDetailPage />} />
        <Route path="/groups/g-copy" element={<p>The copy</p>} />
      </Routes>,
      { route: '/groups/g-1' },
    )

    await screen.findByRole('heading', { name: 'Engineering' })
    await userEvent.click(screen.getByRole('button', { name: /copy group/i }))
    const dialog = await screen.findByRole('dialog')
    const name = within(dialog).getByRole('textbox')
    expect(name).toHaveValue('Engineering (copy)')
    await userEvent.clear(name)
    await userEvent.type(name, 'Platform')
    await userEvent.click(within(dialog).getByRole('button', { name: 'Copy' }))

    await waitFor(() => expect(body).toEqual({ name: 'Platform' }))
    expect(await screen.findByText('The copy')).toBeInTheDocument()
  })

  test('only an admin is offered it', async () => {
    server.use(
      http.get('/v1/portal/groups/:groupId', () => HttpResponse.json(GROUP_DATA)),
      http.get('/v1/portal/me', () =>
        HttpResponse.json({ user_id: 'u-9', email: 'm@ember.sh', role: 'member', org_id: 'demo-org-001' }),
      ),
    )
    renderWithProviders(
      <Routes>
        <Route path="/groups/:id" element={<GroupDetailPage />} />
      </Routes>,
      { route: '/groups/g-1' },
    )

    await screen.findByRole('heading', { name: 'Engineering' })
    expect(screen.queryByRole('button', { name: /copy group/i })).not.toBeInTheDocument()
  })
})
