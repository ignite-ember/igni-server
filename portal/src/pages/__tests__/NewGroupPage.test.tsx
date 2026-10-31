/**
 * Starting a group from another one.
 *
 * A group is exactly what its members get, so "the same as that team,
 * plus this" can only be said by copying. Any group is a template,
 * including the Default one that holds what igni ships — which is what
 * makes "the standard set, minus the coding agents" two ordinary steps.
 *
 * Empty is the default choice on purpose: copying 19 agents into a
 * group somebody meant to fill themselves is a tedious thing to undo.
 */

import { afterEach, describe, expect, test } from 'vitest'
import { http, HttpResponse } from 'msw'
import userEvent from '@testing-library/user-event'
import { screen, cleanup, waitFor } from '@testing-library/react'
import { renderWithProviders } from '../../test/render'
import { server } from '../../test/server'
import { NewGroupPage } from '../NewGroupPage'

afterEach(() => {
  cleanup()
  server.resetHandlers()
})

const GROUPS = {
  groups: [
    {
      id: 'g-default',
      org_id: 'demo-org-001',
      name: 'Default',
      description: null,
      default_model: null,
      created_by_user_id: 'u1',
      created_at: '2026-01-01T00:00:00Z',
      updated_at: '2026-01-01T00:00:00Z',
      is_default: true,
      member_count: 3,
      entry_count: 19,
    },
    {
      id: 'g-legal',
      org_id: 'demo-org-001',
      name: 'Legal',
      description: null,
      default_model: null,
      created_by_user_id: 'u1',
      created_at: '2026-01-02T00:00:00Z',
      updated_at: '2026-01-02T00:00:00Z',
      is_default: false,
      member_count: 1,
      entry_count: 2,
    },
  ],
  total: 2,
}

function renderPage() {
  server.use(http.get('/v1/portal/groups', () => HttpResponse.json(GROUPS)))
  renderWithProviders(<NewGroupPage />, { route: '/groups/new' })
}

function capturePost() {
  const captured: { body: Record<string, unknown> | null } = { body: null }
  server.use(
    http.post('/v1/portal/groups', async ({ request }) => {
      captured.body = (await request.json()) as Record<string, unknown>
      return HttpResponse.json({ id: 'g-new', name: 'Platform' }, { status: 201 })
    }),
  )
  return captured
}

describe('starting from a template', () => {
  test('every existing group is offered', async () => {
    renderPage()

    expect(await screen.findByRole('option', { name: /Copy of Default/i })).toBeInTheDocument()
    expect(screen.getByRole('option', { name: /Copy of Legal/i })).toBeInTheDocument()
  })

  test('the entry count is shown, because that is what you are copying', async () => {
    renderPage()

    expect(await screen.findByRole('option', { name: /Copy of Default.*19 entries/i })).toBeInTheDocument()
  })

  test('the standard set says what it is', async () => {
    // Named by a flag, not by being called "Default" — an admin who
    // renames it keeps it.
    renderPage()

    expect(await screen.findByRole('option', { name: /what igni ships/i })).toBeInTheDocument()
  })

  test('empty is the default choice', async () => {
    renderPage()

    const select = (await screen.findByLabelText(/start from/i)) as HTMLSelectElement
    expect(select.value).toBe('')
  })

  test('an empty group sends no template', async () => {
    const captured = capturePost()
    const user = userEvent.setup()
    renderPage()

    await user.type(await screen.findByLabelText(/^name$/i), 'Platform')
    await user.click(screen.getByRole('button', { name: /create group/i }))

    await waitFor(() => expect(captured.body).not.toBeNull())
    expect(captured.body!.template_group_id).toBeUndefined()
  })

  test('choosing one sends its id', async () => {
    const captured = capturePost()
    const user = userEvent.setup()
    renderPage()

    await user.type(await screen.findByLabelText(/^name$/i), 'Platform')
    await user.selectOptions(await screen.findByLabelText(/start from/i), 'g-default')
    await user.click(screen.getByRole('button', { name: /create group/i }))

    await waitFor(() => expect(captured.body).not.toBeNull())
    expect(captured.body!.template_group_id).toBe('g-default')
  })
})
