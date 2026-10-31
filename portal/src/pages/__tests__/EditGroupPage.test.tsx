/**
 * EditGroupPage — the form, and the three things it refuses to do.
 *
 * Editing a group is a small screen with a large number of ways to be
 * quietly wrong, and every one of them shows the user something
 * plausible:
 *
 * * **the shipped default group takes no edits.** The server refuses
 *   them, so a form that cannot be saved is worse than none — the page
 *   says so instead and offers the thing that does work, copying it.
 *   If that guard goes, the user fills in a form and gets a 4xx;
 * * **an empty name is caught here**, before the request, because the
 *   server's refusal arrives as a generic failure and the field that
 *   caused it is on screen;
 * * **a server error shows the server's own reason.** The mutation digs
 *   `detail` out of the response and falls back to a generic sentence,
 *   and a fallback that swallowed the specific message would turn "that
 *   name is taken" into "could not save".
 *
 * Plus the seeding trick the page's own comment flags: the fields are
 * `null` until edited so they show the group's values without an
 * effect. The consequence worth pinning is that **clearing a field
 * leaves it clear** — `'' ?? group.name` is `''`, not the group's name
 * — because a field that silently refilled itself would be maddening
 * and is exactly what a naive `||` would do.
 */
import { afterEach, describe, expect, test, vi } from 'vitest'
import { http, HttpResponse } from 'msw'
import userEvent from '@testing-library/user-event'
import { cleanup, screen, waitFor } from '@testing-library/react'
import { Route, Routes } from 'react-router-dom'
import { renderWithProviders } from '../../test/render'
import { server } from '../../test/server'
import { EditGroupPage } from '../EditGroupPage'

afterEach(() => {
  cleanup()
  server.resetHandlers()
  vi.restoreAllMocks()
})

const GROUP = {
  id: 'g-1',
  org_id: 'demo-org-001',
  name: 'Engineering',
  description: 'Engineering team',
  created_by_user_id: 'demo-user-001',
  created_at: '2026-01-01T00:00:00Z',
  updated_at: '2026-01-01T00:00:00Z',
  member_count: 0,
  entry_count: 0,
}

function renderPage() {
  return renderWithProviders(
    <Routes>
      <Route path="/groups/:id/edit" element={<EditGroupPage />} />
      <Route path="/groups/:id" element={<div>group detail stub</div>} />
      <Route path="/groups" element={<div>groups list stub</div>} />
    </Routes>,
    { route: '/groups/g-1/edit' },
  )
}

describe('the form shows what the group says', () => {
  test('name and description are seeded from the group', async () => {
    renderPage()

    expect(await screen.findByDisplayValue('Engineering')).toBeInTheDocument()
    expect(screen.getByDisplayValue('Engineering team')).toBeInTheDocument()
  })

  test('clearing a field leaves it clear', async () => {
    // The seeding is `name ?? group.name`, so an edited-to-empty field
    // stays empty. A `||` there would refill it from the group as soon
    // as the user deleted the last character.
    const user = userEvent.setup()
    renderPage()

    const name = await screen.findByDisplayValue('Engineering')
    await user.clear(name)

    expect(name).toHaveValue('')
  })
})

describe('what it refuses', () => {
  test('the shipped default group cannot be edited', async () => {
    server.use(
      http.get('/v1/portal/groups/:groupId', () =>
        HttpResponse.json({ ...GROUP, is_default: true }),
      ),
    )
    renderPage()

    expect(await screen.findByText(/This is what igni ships/i)).toBeInTheDocument()
    // And no form, because the point is that saving is impossible.
    expect(screen.queryByDisplayValue('Engineering')).not.toBeInTheDocument()
    // The route that does work is offered instead.
    expect(screen.getByRole('link', { name: /New group from it/i })).toBeInTheDocument()
  })

  test('an empty name is refused before a request is made', async () => {
    const user = userEvent.setup()
    let patched = false
    server.use(
      http.patch('/v1/portal/groups/:groupId', () => {
        patched = true
        return HttpResponse.json(GROUP)
      }),
    )
    renderPage()

    await user.clear(await screen.findByDisplayValue('Engineering'))
    await user.click(screen.getByRole('button', { name: /save/i }))

    expect(await screen.findByText(/A group needs a name/i)).toBeInTheDocument()
    expect(patched).toBe(false)
  })

  test('a missing group says so rather than showing an empty form', async () => {
    server.use(
      http.get('/v1/portal/groups/:groupId', () => new HttpResponse(null, { status: 404 })),
    )
    renderPage()

    expect(await screen.findByText(/Group not found/i)).toBeInTheDocument()
  })
})

describe('saving', () => {
  test('sends the trimmed values and leaves for the group', async () => {
    const user = userEvent.setup()
    let body: unknown = null
    server.use(
      http.patch('/v1/portal/groups/:groupId', async ({ request }) => {
        body = await request.json()
        return HttpResponse.json(GROUP)
      }),
    )
    renderPage()

    const name = await screen.findByDisplayValue('Engineering')
    await user.clear(name)
    await user.type(name, '  Platform  ')
    await user.click(screen.getByRole('button', { name: /save/i }))

    // Navigating away is how the user knows it worked.
    expect(await screen.findByText('group detail stub')).toBeInTheDocument()
    expect(body).toMatchObject({ name: 'Platform' })
  })

  test('an empty description is sent as null, not as an empty string', async () => {
    // The page does `description.trim() || null`. Worth pinning because
    // the two are different to a database column and the difference is
    // invisible on screen.
    const user = userEvent.setup()
    let body: { description?: unknown } = {}
    server.use(
      http.patch('/v1/portal/groups/:groupId', async ({ request }) => {
        body = (await request.json()) as { description?: unknown }
        return HttpResponse.json(GROUP)
      }),
    )
    renderPage()

    await user.clear(await screen.findByDisplayValue('Engineering team'))
    await user.click(screen.getByRole('button', { name: /save/i }))

    await waitFor(() => expect(body.description).not.toBeUndefined())
    expect(body.description).toBeNull()
  })

  test("a refusal shows the server's own reason", async () => {
    const user = userEvent.setup()
    server.use(
      http.patch('/v1/portal/groups/:groupId', () =>
        HttpResponse.json({ detail: 'A group called Platform already exists' }, { status: 409 }),
      ),
    )
    renderPage()

    await screen.findByDisplayValue('Engineering')
    await user.click(screen.getByRole('button', { name: /save/i }))

    expect(await screen.findByText(/A group called Platform already exists/i)).toBeInTheDocument()
    // Still on the form, because the edit is not lost.
    expect(screen.getByDisplayValue('Engineering')).toBeInTheDocument()
  })

  test('a refusal with no detail still says something', async () => {
    const user = userEvent.setup()
    server.use(
      http.patch('/v1/portal/groups/:groupId', () => new HttpResponse(null, { status: 500 })),
    )
    renderPage()

    await screen.findByDisplayValue('Engineering')
    await user.click(screen.getByRole('button', { name: /save/i }))

    expect(await screen.findByText(/Could not save the group/i)).toBeInTheDocument()
  })
})
