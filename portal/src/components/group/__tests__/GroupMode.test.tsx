/**
 * Where igni runs for a group: three choices, and a warning before the
 * one that disconnects people from the cloud.
 */
import { describe, expect, test, vi } from 'vitest'
import { http, HttpResponse } from 'msw'
import userEvent from '@testing-library/user-event'
import { Route, Routes } from 'react-router-dom'
import { screen, waitFor } from '@testing-library/react'
import { renderWithProviders } from '../../../test/render'
import { server } from '../../../test/server'
import { GroupMode } from '../GroupMode'
import { SettingsTab } from '../SettingsTab'
import type { GroupMode as Mode, OrgGroupDetail } from '../../../types'

/** Every PATCH body the card sends, in order. */
function recordPatches(answer: () => Response = () => HttpResponse.json({})) {
  const sent: unknown[] = []
  server.use(
    http.patch('/v1/portal/groups/g-1', async ({ request }) => {
      sent.push(await request.json())
      return answer()
    }),
  )
  return sent
}

function renderMode(current: Mode | undefined, { isAdmin = true } = {}) {
  const onFeedback = vi.fn()
  renderWithProviders(<GroupMode groupId="g-1" current={current} isAdmin={isAdmin} onFeedback={onFeedback} />)
  return onFeedback
}

async function pick(current: string, choice: string) {
  await userEvent.click(await screen.findByRole('button', { name: current }))
  await userEvent.click(await screen.findByRole('menuitemradio', { name: new RegExp(`^${choice}`) }))
}

describe('GroupMode', () => {
  test('offers the three places igni can run, each saying what it means, the current one selected', async () => {
    renderMode('local_and_cloud')
    await userEvent.click(await screen.findByRole('button', { name: 'This computer and the cloud' }))

    const both = await screen.findByRole('menuitemradio', { name: /^This computer and the cloud/ })
    const local = screen.getByRole('menuitemradio', { name: /^This computer only/ })
    const cloud = screen.getByRole('menuitemradio', { name: /^Cloud only/ })
    expect(both).toHaveAttribute('aria-checked', 'true')
    expect(local).toHaveAttribute('aria-checked', 'false')
    expect(cloud).toHaveAttribute('aria-checked', 'false')
    expect(local).toHaveTextContent(/cloud igni is switched off/i)
    expect(cloud).toHaveTextContent(/nothing runs on their computers/i)
    expect(both).toHaveTextContent(/choose in the desktop app/i)
  })

  test('a group from a server older than the field reads as both, which is what it is', async () => {
    renderMode(undefined)

    expect(await screen.findByRole('button', { name: 'This computer and the cloud' })).toBeInTheDocument()
  })

  test('a member sees the choice and what it means, with nothing to change', async () => {
    const sent = recordPatches()
    renderMode('cloud', { isAdmin: false })

    expect(await screen.findByText('Cloud only')).toBeInTheDocument()
    expect(screen.getByText(/nothing runs on their computers/i)).toBeInTheDocument()
    expect(screen.queryByRole('button')).not.toBeInTheDocument()
    expect(sent).toEqual([])
  })

  test('a change that disconnects nobody is saved at once', async () => {
    const sent = recordPatches()
    const onFeedback = renderMode('local')

    await pick('This computer only', 'This computer and the cloud')

    await waitFor(() => expect(sent).toEqual([{ mode: 'local_and_cloud' }]))
    expect(screen.queryByRole('alertdialog')).not.toBeInTheDocument()
    await waitFor(() =>
      expect(onFeedback).toHaveBeenCalledWith('success', 'People in this group choose between their computer and the cloud'),
    )
  })

  test('moving to the cloud only says people on their computer move when their run ends', async () => {
    const sent = recordPatches()
    const onFeedback = renderMode('local_and_cloud')

    await pick('This computer and the cloud', 'Cloud only')

    await waitFor(() => expect(sent).toEqual([{ mode: 'cloud' }]))
    await waitFor(() =>
      expect(onFeedback).toHaveBeenCalledWith('success', expect.stringMatching(/move when their current run ends/)),
    )
  })

  test('moving to the cloud from computers only says so plainly', async () => {
    const sent = recordPatches()
    const onFeedback = renderMode('local')

    await pick('This computer only', 'Cloud only')

    await waitFor(() => expect(sent).toEqual([{ mode: 'cloud' }]))
    await waitFor(() => expect(onFeedback).toHaveBeenCalledWith('success', 'Group runs igni in the cloud only'))
  })

  test('moving to computers only warns first, and Cancel saves nothing', async () => {
    const sent = recordPatches()
    const onFeedback = renderMode('cloud')

    await pick('Cloud only', 'This computer only')

    const dialog = await screen.findByRole('alertdialog', { name: /computers only/i })
    expect(dialog).toHaveTextContent(/disconnected at once/i)
    await userEvent.click(screen.getByRole('button', { name: 'Cancel' }))

    await waitFor(() => expect(screen.queryByRole('alertdialog')).not.toBeInTheDocument())
    expect(sent).toEqual([])
    expect(onFeedback).not.toHaveBeenCalled()
  })

  test('confirming the warning saves the change', async () => {
    const sent = recordPatches()
    const onFeedback = renderMode('local_and_cloud')

    await pick('This computer and the cloud', 'This computer only')
    await screen.findByRole('alertdialog')
    await userEvent.click(screen.getByRole('button', { name: 'Switch off the cloud' }))

    await waitFor(() => expect(sent).toEqual([{ mode: 'local' }]))
    await waitFor(() =>
      expect(onFeedback).toHaveBeenCalledWith('success', "Group runs igni on people's computers only"),
    )
  })

  test('choosing what it already is sends nothing', async () => {
    const sent = recordPatches()
    const onFeedback = renderMode('local')

    await pick('This computer only', 'This computer only')

    expect(screen.queryByRole('alertdialog')).not.toBeInTheDocument()
    expect(sent).toEqual([])
    expect(onFeedback).not.toHaveBeenCalled()
  })

  test("a refusal says the server's sentence", async () => {
    recordPatches(() => HttpResponse.json({ detail: 'Only an admin can change that.' }, { status: 403 }))
    const onFeedback = renderMode('local_and_cloud')

    await pick('This computer and the cloud', 'Cloud only')

    await waitFor(() => expect(onFeedback).toHaveBeenCalledWith('critical', 'Only an admin can change that.'))
  })

  test('a refusal with no sentence says what failed', async () => {
    recordPatches(() => new HttpResponse(null, { status: 500 }))
    const onFeedback = renderMode('local_and_cloud')

    await pick('This computer and the cloud', 'Cloud only')

    await waitFor(() => expect(onFeedback).toHaveBeenCalledWith('critical', 'Failed to change where igni runs'))
  })
})

describe('the card on the Settings tab', () => {
  const GROUP: OrgGroupDetail = {
    id: 'g-1',
    org_id: 'demo-org-001',
    name: 'Default',
    description: null,
    default_model: null,
    theme: null,
    is_default: true,
    mode: 'local_and_cloud',
    created_by_user_id: 'demo-user-001',
    created_at: '2026-01-01T00:00:00Z',
    updated_at: '2026-01-01T00:00:00Z',
    member_count: 3,
    entry_count: 0,
  }

  function renderTab(isAdmin: boolean) {
    server.use(
      http.get('/v1/portal/groups/g-1/entries', () => HttpResponse.json({ entries: [], total: 0 })),
      http.get('/v1/portal/inference/models', () => HttpResponse.json({ models: [] })),
    )
    renderWithProviders(
      <Routes>
        <Route
          path="/groups/:id"
          element={<SettingsTab group={GROUP} isAdmin={isAdmin} active onFeedback={vi.fn()} />}
        />
      </Routes>,
      { route: '/groups/g-1' },
    )
  }

  test('an admin may change it on the Default group, whose other settings stay read-only', async () => {
    const sent = recordPatches()
    renderTab(true)

    expect(await screen.findByText('Where igni runs for this group')).toBeInTheDocument()
    // The Default group's description has no Edit, and its model no menu…
    expect(screen.queryByRole('link', { name: /edit/i })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Deployment default' })).not.toBeInTheDocument()
    // …but where igni runs is a deployment choice, not what igni ships.
    await pick('This computer and the cloud', 'Cloud only')
    await waitFor(() => expect(sent).toEqual([{ mode: 'cloud' }]))
  })

  test('a member sees where igni runs and cannot change it', async () => {
    renderTab(false)

    expect(await screen.findByText('Where igni runs for this group')).toBeInTheDocument()
    expect(screen.getByText('This computer and the cloud')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'This computer and the cloud' })).not.toBeInTheDocument()
  })
})
