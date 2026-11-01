/**
 * Erasures: former members' and discarded apps' VMs on their way to
 * erasure, and discarding a former member's now.
 */
import { describe, expect, test } from 'vitest'
import { http, HttpResponse } from 'msw'
import userEvent from '@testing-library/user-event'
import { screen } from '@testing-library/react'
import { renderWithProviders } from '../../test/render'
import { server } from '../../test/server'
import { ErasuresPage } from '../ErasuresPage'
import type { Erasure } from '../../types'

const ADA: Erasure = {
  producer: { kind: 'person', id: 'u-ada' },
  name: 'ada@acme.com',
  incarnation: 'm-1',
  removed_at: '2026-10-20T10:00:00Z',
  erase_at: '2026-11-19T10:00:00Z',
  state: 'scheduled',
  erased_at: null,
  legal_hold: false,
  exports: 0,
  discarded_by_user_id: null,
  discarded_at: null,
}
const BOT: Erasure = { ...ADA, producer: { kind: 'app', id: 'app-1' }, name: 'Review bot', incarnation: 'app-1', state: 'erasing' }
const OLD: Erasure = { ...BOT, producer: { kind: 'app', id: 'app-0' }, name: 'Old bot', incarnation: 'app-0', state: 'erased', erased_at: '2026-10-30T10:00:00Z' }
const HELD: Erasure = { ...ADA, producer: { kind: 'person', id: 'u-max' }, name: 'max@acme.com', state: 'held', erase_at: null, legal_hold: true }

function list(erasures: Erasure[]) {
  return http.get('/v1/portal/orgs/demo-org-001/erasures', () => HttpResponse.json({ org_id: 'demo-org-001', erasures }))
}

describe('ErasuresPage', () => {
  test('shows each state in plain words', async () => {
    server.use(list([ADA, BOT, OLD, HELD]))

    renderWithProviders(<ErasuresPage />)

    expect(await screen.findByText(/^Erase on /)).toBeInTheDocument()
    expect(screen.getByText('Erasing…')).toBeInTheDocument()
    expect(screen.getByText(/^Erased /)).toBeInTheDocument()
    expect(screen.getByText('Held (legal hold)')).toBeInTheDocument()
  })

  test('offers discard now only for a former member waiting out the retention', async () => {
    server.use(list([ADA, BOT, OLD, HELD]))

    renderWithProviders(<ErasuresPage />)

    expect(await screen.findAllByRole('button', { name: /Discard now/ })).toHaveLength(1)
    expect(screen.getByRole('button', { name: 'Discard now ada@acme.com' })).toBeInTheDocument()
  })

  test('discards a former member now after the confirm', async () => {
    let sent: unknown = null
    server.use(
      list([ADA]),
      http.post('/v1/portal/orgs/demo-org-001/members/u-ada/discard', async ({ request }) => {
        sent = await request.json()
        return HttpResponse.json({ erasures: [{ ...ADA, state: 'erasing' }] })
      }),
    )

    renderWithProviders(<ErasuresPage />)
    await userEvent.click(await screen.findByRole('button', { name: 'Discard now ada@acme.com' }))
    expect(await screen.findByText(/cannot be recovered/)).toBeInTheDocument()
    expect(screen.queryByText(/ends the chance to take another/)).not.toBeInTheDocument()
    await userEvent.click(screen.getAllByRole('button', { name: 'Discard now' }).at(-1)!)

    await expect.poll(() => sent).toEqual({ incarnation: 'm-1' })
  })

  test('warns that discarding ends the chance to export', async () => {
    server.use(list([{ ...ADA, exports: 1 }]))

    renderWithProviders(<ErasuresPage />)
    await userEvent.click(await screen.findByRole('button', { name: 'Discard now ada@acme.com' }))

    expect(await screen.findByText(/discarding now ends the chance to take another/)).toBeInTheDocument()
  })

  test("shows the server's refusal: a legal hold", async () => {
    server.use(
      list([ADA]),
      http.post('/v1/portal/orgs/demo-org-001/members/u-ada/discard', () =>
        HttpResponse.json({ detail: 'A legal hold stands on this person here: Litigation 2026-17' }, { status: 409 }),
      ),
    )

    renderWithProviders(<ErasuresPage />)
    await userEvent.click(await screen.findByRole('button', { name: 'Discard now ada@acme.com' }))
    await userEvent.click(screen.getAllByRole('button', { name: 'Discard now' }).at(-1)!)

    expect(await screen.findByText(/A legal hold stands on this person here/)).toBeInTheDocument()
  })

  test('says so when nothing is waiting', async () => {
    server.use(list([]))

    renderWithProviders(<ErasuresPage />)

    expect(await screen.findByText('Nothing is waiting for erasure.')).toBeInTheDocument()
  })
})
