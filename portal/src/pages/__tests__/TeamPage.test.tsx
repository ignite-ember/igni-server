/**
 * TeamPage — covers the observable shape of the members list:
 *  - members render with name/email + role + status badges
 *  - the current user's row hides the actions kebab (no self-edit)
 *  - members (non-admins) see no actions column AND no InviteForm
 */
import { describe, expect, test, vi } from 'vitest'
import { http, HttpResponse } from 'msw'
import userEvent from '@testing-library/user-event'
import { screen, within } from '@testing-library/react'
import { renderWithProviders } from '../../test/render'
import { server } from '../../test/server'
import { TeamPage } from '../TeamPage'

function meAs(role: 'admin' | 'member', userId = 'demo-user-001') {
  return {
    user_id: userId,
    email: 'me@ember.sh',
    full_name: 'Me',
    created_at: '2026-01-01T00:00:00Z',
    role,
    pending_email: null,
    auth_provider: 'email',
    org_id: 'demo-org-001',
    org_slug: 'demo',
    org_display_name: 'Demo Org',
    organizations: [
      { id: 'demo-org-001', slug: 'demo', display_name: 'Demo Org', role },
    ],
  }
}

const TEAM_MEMBERS = {
  members: [
    {
      id: 'demo-user-001',
      user_id: 'demo-user-001',
      email: 'me@ember.sh',
      full_name: 'Me',
      role: 'admin',
      status: 'active',
      total_requests: 12,
      total_tokens: 4500,
      last_active: '2026-05-28T12:00:00Z',
      joined_at: '2026-01-01T00:00:00Z',
      assigned_by: null,
    },
    {
      id: 'u-2',
      user_id: 'u-2',
      email: 'bob@ember.sh',
      full_name: 'Bob',
      role: 'member',
      status: 'active',
      total_requests: 0,
      total_tokens: 0,
      last_active: null,
      joined_at: '2026-02-01T00:00:00Z',
      assigned_by: 'demo-user-001',
    },
    {
      id: 'u-3',
      user_id: 'u-3',
      email: 'pending@ember.sh',
      full_name: null,
      role: 'member',
      status: 'invited',
      total_requests: 0,
      total_tokens: 0,
      last_active: null,
      joined_at: '2026-05-01T00:00:00Z',
      assigned_by: 'demo-user-001',
    },
  ],
  total: 3,
}

describe('TeamPage', () => {
  test('renders members with role + status badges (admin view)', async () => {
    server.use(
      http.get('/v1/portal/me', () => HttpResponse.json(meAs('admin'))),
      http.get('/v1/portal/team', () => HttpResponse.json(TEAM_MEMBERS)),
    )

    renderWithProviders(<TeamPage />)

    expect(await screen.findByText('Bob')).toBeInTheDocument()
    expect(screen.getByText('bob@ember.sh')).toBeInTheDocument()
    expect(screen.getByText('pending@ember.sh')).toBeInTheDocument()
    // Role badges (each role label appears at least once across rows)
    expect(screen.getAllByText(/^Admin$/i).length).toBeGreaterThan(0)
    expect(screen.getAllByText(/^Member$/i).length).toBeGreaterThan(0)
    // Status badges
    expect(screen.getAllByText(/^Active$/i).length).toBeGreaterThan(0)
    expect(screen.getByText(/^Invited$/i)).toBeInTheDocument()
  })

  test('the usage column counts requests, which is what it always showed', async () => {
    server.use(
      http.get('/v1/portal/me', () => HttpResponse.json(meAs('admin'))),
      http.get('/v1/portal/team', () => HttpResponse.json(TEAM_MEMBERS)),
    )

    renderWithProviders(<TeamPage />)
    await screen.findByText('Bob')

    expect(screen.getByRole('columnheader', { name: 'Requests (30d)' })).toBeInTheDocument()
    expect(screen.queryByRole('columnheader', { name: /Sessions/ })).not.toBeInTheDocument()
    expect(screen.getByText('12')).toBeInTheDocument()
  })

  test('admin sees actions kebab on every row (including self)', async () => {
    server.use(
      http.get('/v1/portal/me', () => HttpResponse.json(meAs('admin'))),
      http.get('/v1/portal/team', () => HttpResponse.json(TEAM_MEMBERS)),
    )

    renderWithProviders(<TeamPage />)

    await screen.findByText('Bob')

    expect(screen.getByRole('button', { name: /Actions for Bob/i })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /Actions for Me/i })).toBeInTheDocument()
  })

  test('self row menu offers nothing — role, status and remove are all blocked on self', async () => {
    server.use(
      http.get('/v1/portal/me', () => HttpResponse.json(meAs('admin'))),
      http.get('/v1/portal/team', () => HttpResponse.json(TEAM_MEMBERS)),
    )

    renderWithProviders(<TeamPage />)
    await screen.findByText('Bob')

    await userEvent.click(screen.getByRole('button', { name: /Actions for Me/i }))
    const menu = await screen.findByRole('menu')
    // Every action is gated on !isSelf at the backend, so the self-row
    // menu is deliberately empty rather than offering something that
    // would 4xx.
    expect(within(menu).queryByText(/Change role/i)).not.toBeInTheDocument()
    expect(within(menu).queryByText(/Deactivate/i)).not.toBeInTheDocument()
    expect(within(menu).queryByText(/Remove from team/i)).not.toBeInTheDocument()
    // B5's action is allowed on self by the backend, but a table row is
    // the wrong place to sign *yourself* out of — Settings → Sessions is
    // where that lives, with the wording that says you go too.
    expect(within(menu).queryByText(/Sign out everywhere/i)).not.toBeInTheDocument()
  })

  test('a member menu never stacks two dividers between groups', async () => {
    // The role group used to close with a divider and the status item
    // open with another, drawing a double rule between them.
    server.use(
      http.get('/v1/portal/me', () => HttpResponse.json(meAs('admin'))),
      http.get('/v1/portal/team', () => HttpResponse.json(TEAM_MEMBERS)),
    )

    renderWithProviders(<TeamPage />)
    await screen.findByText('Bob')

    await userEvent.click(screen.getByRole('button', { name: /Actions for Bob/i }))
    const menu = await screen.findByRole('menu')
    const dividers = [...menu.querySelectorAll('[data-component="ActionList.Divider"]')]
    const doubled = dividers.filter(
      (divider) => divider.nextElementSibling?.getAttribute('data-component') === 'ActionList.Divider',
    )
    expect(dividers.length).toBeGreaterThan(0)
    expect(doubled).toHaveLength(0)
  })

  test('invited row exposes no Reactivate / Deactivate action', async () => {
    server.use(
      http.get('/v1/portal/me', () => HttpResponse.json(meAs('admin'))),
      http.get('/v1/portal/team', () => HttpResponse.json(TEAM_MEMBERS)),
    )

    renderWithProviders(<TeamPage />)
    await screen.findByText('Bob')

    // u-3 (pending@ember.sh) is invited — open its kebab
    await userEvent.click(
      screen.getByRole('button', { name: /Actions for pending@ember\.sh/i }),
    )
    const menu = await screen.findByRole('menu')
    expect(within(menu).queryByText(/Reactivate/i)).not.toBeInTheDocument()
    expect(within(menu).queryByText(/Deactivate/i)).not.toBeInTheDocument()
    // Role change + Remove are still there
    expect(within(menu).getByText(/Change role/i)).toBeInTheDocument()
    expect(within(menu).getByText(/Remove from team/i)).toBeInTheDocument()
  })

  test('admin sees InviteForm', async () => {
    server.use(
      http.get('/v1/portal/me', () => HttpResponse.json(meAs('admin'))),
      http.get('/v1/portal/team', () => HttpResponse.json(TEAM_MEMBERS)),
    )

    renderWithProviders(<TeamPage />)

    expect(await screen.findByPlaceholderText(/colleague@company.com/i)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /Send invite/i })).toBeInTheDocument()
  })

  test('member (non-admin) sees no InviteForm and no actions column', async () => {
    server.use(
      http.get('/v1/portal/me', () => HttpResponse.json(meAs('member'))),
      http.get('/v1/portal/team', () => HttpResponse.json(TEAM_MEMBERS)),
    )

    renderWithProviders(<TeamPage />)

    // Wait for any member-row data
    await screen.findByText('Bob')

    expect(screen.queryByPlaceholderText(/colleague@company.com/i)).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /Send invite/i })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /^Actions for /i })).not.toBeInTheDocument()
  })

  test('MemberStats summary counts reflect the team', async () => {
    server.use(
      http.get('/v1/portal/me', () => HttpResponse.json(meAs('admin'))),
      http.get('/v1/portal/team', () => HttpResponse.json(TEAM_MEMBERS)),
    )

    renderWithProviders(<TeamPage />)

    // Wait for the page to settle
    await screen.findByText('Bob')

    // 2 active members (Me + Bob)
    const activeStat = screen.getByText('Active members').closest('.team-stat') as HTMLElement
    expect(within(activeStat).getByText('2')).toBeInTheDocument()

    // 1 pending invite
    const pendingStat = screen.getByText('Pending invites').closest('.team-stat') as HTMLElement
    expect(within(pendingStat).getByText('1')).toBeInTheDocument()

    // 1 admin
    const adminStat = screen.getByText('Admins').closest('.team-stat') as HTMLElement
    expect(within(adminStat).getByText('1')).toBeInTheDocument()
  })

  test('blankslate appears when team is empty', async () => {
    server.use(
      http.get('/v1/portal/me', () => HttpResponse.json(meAs('admin'))),
      http.get('/v1/portal/team', () => HttpResponse.json({ members: [], total: 0 })),
    )

    renderWithProviders(<TeamPage />)

    expect(await screen.findByText(/No team members yet/i)).toBeInTheDocument()
  })

  test('a member row offers Sign out everywhere, and asks first', async () => {
    // DEC-10/B5. Deactivating stops them reaching this organisation;
    // this withdraws the credential itself. Both are in the same menu,
    // one item apart, which is why it is behind a confirmation.
    let calls = 0
    server.use(
      http.get('/v1/portal/me', () => HttpResponse.json(meAs('admin'))),
      http.get('/v1/portal/team', () => HttpResponse.json(TEAM_MEMBERS)),
      http.post('/v1/portal/team/:userId/revoke-sessions', () => {
        calls += 1
        return HttpResponse.json({ user_id: 'u-2', tokens_valid_after: '2026-09-03T12:00:01Z' })
      }),
    )

    renderWithProviders(<TeamPage />)
    await screen.findByText('Bob')

    await userEvent.click(screen.getByRole('button', { name: /Actions for Bob/i }))
    const menu = await screen.findByRole('menu')
    await userEvent.click(within(menu).getByText(/Sign out everywhere/i))

    expect(await screen.findByText(/stops working immediately/i)).toBeInTheDocument()
    expect(calls).toBe(0)
  })

  test('confirming signs that member out and reports it', async () => {
    let target = ''
    server.use(
      http.get('/v1/portal/me', () => HttpResponse.json(meAs('admin'))),
      http.get('/v1/portal/team', () => HttpResponse.json(TEAM_MEMBERS)),
      http.post('/v1/portal/team/:userId/revoke-sessions', ({ params }) => {
        target = String(params.userId)
        return HttpResponse.json({ user_id: target, tokens_valid_after: '2026-09-03T12:00:01Z' })
      }),
    )

    renderWithProviders(<TeamPage />)
    await screen.findByText('Bob')

    await userEvent.click(screen.getByRole('button', { name: /Actions for Bob/i }))
    const menu = await screen.findByRole('menu')
    await userEvent.click(within(menu).getByText(/Sign out everywhere/i))

    const dialog = await screen.findByRole('alertdialog')
    await userEvent.click(within(dialog).getByRole('button', { name: /Sign out everywhere/i }))

    // The id from the row, not the signed-in admin's: a mix-up here
    // would sign the operator out and leave the suspect logged in.
    await vi.waitFor(() => expect(target).toBe('u-2'))
    expect(await screen.findByText(/Signed out of every device/i)).toBeInTheDocument()
  })
})
