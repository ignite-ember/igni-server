/**
 * One app: its own extension switches and its VM profile.
 */
import { describe, expect, test } from 'vitest'
import { http, HttpResponse } from 'msw'
import userEvent from '@testing-library/user-event'
import { screen } from '@testing-library/react'
import { Route, Routes } from 'react-router-dom'
import { renderWithProviders } from '../../test/render'
import { server } from '../../test/server'
import { AppDetailPage } from '../AppDetailPage'
import { OVERVIEW } from '../../test/fixtures/vmProfiles'

const BOT = {
  app_id: 'app-1',
  client_id: 'app-1',
  name: 'Review bot',
  owner_user_id: 'demo-user-001',
  owner_email: 'alex@ember.sh',
  thumbprint: 'thumb-1',
  vm_profile_id: null,
  created_at: '2026-10-26T10:00:00Z',
}

function page() {
  return renderWithProviders(
    <Routes>
      <Route path="/apps/:appId" element={<AppDetailPage />} />
      <Route path="/apps" element={<p>The apps</p>} />
    </Routes>,
    { route: '/apps/app-1' },
  )
}

describe('AppDetailPage', () => {
  test('switches an extension on for the app', async () => {
    let switched: unknown = null
    server.use(
      http.get('/v1/portal/apps/app-1', () => HttpResponse.json(BOT)),
      http.get('/v1/portal/apps/app-1/extensions', () =>
        HttpResponse.json({
          extensions: [
            { extension_id: 'igni.codeindex', name: 'CodeIndex', description: '', service_url: 'https://c', enabled: false },
          ],
        }),
      ),
      http.get('/v1/portal/orgs/demo-org-001/vm-profiles', () => HttpResponse.json(OVERVIEW)),
      http.put('/v1/portal/apps/app-1/extensions/igni.codeindex', async ({ request }) => {
        switched = await request.json()
        return new HttpResponse(null, { status: 204 })
      }),
    )

    page()
    await userEvent.click(await screen.findByRole('button', { name: 'CodeIndex' }))

    await expect.poll(() => switched).toEqual({ enabled: true })
  })

  test('gives the app a VM profile', async () => {
    let assigned: unknown = null
    server.use(
      http.get('/v1/portal/apps/app-1', () => HttpResponse.json(BOT)),
      http.get('/v1/portal/apps/app-1/extensions', () => HttpResponse.json({ extensions: [] })),
      http.get('/v1/portal/orgs/demo-org-001/vm-profiles', () => HttpResponse.json(OVERVIEW)),
      http.put('/v1/portal/orgs/demo-org-001/apps/app-1/vm-profile', async ({ request }) => {
        assigned = await request.json()
        return new HttpResponse(null, { status: 204 })
      }),
    )

    page()
    const select = await screen.findByRole('combobox', { name: /VM profile/ })
    await screen.findByRole('option', { name: 'Browser' })
    await userEvent.selectOptions(select, 'p-browser')

    await expect.poll(() => assigned).toEqual({ profile_id: 'p-browser' })
  })

  test('enables the cloud for the app without showing its credential', async () => {
    let issued = false
    server.use(
      http.get('/v1/portal/apps/app-1', () => HttpResponse.json(BOT)),
      http.get('/v1/portal/apps/app-1/extensions', () => HttpResponse.json({ extensions: [] })),
      http.get('/v1/portal/orgs/demo-org-001/vm-profiles', () => HttpResponse.json(OVERVIEW)),
      http.post('/v1/portal/apps/app-1/vm-credential', () => {
        issued = true
        return HttpResponse.json({ credential_id: 'f1', awaiting_pickup: true }, { status: 201 })
      }),
    )

    page()
    await userEvent.click(await screen.findByRole('button', { name: /Enable cloud/ }))

    await expect.poll(() => issued).toBe(true)
    expect(await screen.findByText(/nobody sees it/)).toBeInTheDocument()
  })

  function common() {
    server.use(
      http.get('/v1/portal/apps/app-1', () => HttpResponse.json(BOT)),
      http.get('/v1/portal/apps/app-1/extensions', () => HttpResponse.json({ extensions: [] })),
      http.get('/v1/portal/orgs/demo-org-001/vm-profiles', () => HttpResponse.json(OVERVIEW)),
    )
  }

  test('discards the app only after the confirm, saying what goes and what is freed', async () => {
    let sent: unknown = null
    common()
    server.use(
      http.get('/v1/portal/orgs/demo-org-001/legal-holds', () => HttpResponse.json({ holds: [], apps: [] })),
      http.post('/v1/portal/apps/app-1/discard', async ({ request }) => {
        sent = await request.json()
        return HttpResponse.json({ discarded: { state: 'erasing' }, fresh_app: null })
      }),
    )

    page()
    await userEvent.click(await screen.findByRole('button', { name: 'Discard app' }))
    expect(await screen.findByText(/its VM can never be woken again/)).toBeInTheDocument()
    expect(screen.getByText(/Its place under your licence is freed at once/)).toBeInTheDocument()
    expect(sent).toBeNull()
    await userEvent.click(screen.getAllByRole('button', { name: 'Discard app' }).at(-1)!)

    await expect.poll(() => sent).toEqual({ start_fresh: false })
  })

  test('discards and starts a fresh app, opening it', async () => {
    let sent: unknown = null
    common()
    server.use(
      http.get('/v1/portal/orgs/demo-org-001/legal-holds', () => HttpResponse.json({ holds: [], apps: [] })),
      http.post('/v1/portal/apps/app-1/discard', async ({ request }) => {
        sent = await request.json()
        return HttpResponse.json({ discarded: { state: 'erasing' }, fresh_app: { ...BOT, app_id: 'app-2', client_id: 'app-2' } })
      }),
      http.get('/v1/portal/apps/app-2', () => HttpResponse.json({ ...BOT, app_id: 'app-2', client_id: 'app-2', name: 'Fresh bot' })),
      http.get('/v1/portal/apps/app-2/extensions', () => HttpResponse.json({ extensions: [] })),
    )

    page()
    await userEvent.click(await screen.findByRole('button', { name: 'Discard app' }))
    await userEvent.click(await screen.findByRole('checkbox', { name: /Start a fresh app with the same name, key and settings/ }))
    await userEvent.click(screen.getAllByRole('button', { name: 'Discard app' }).at(-1)!)

    await expect.poll(() => sent).toEqual({ start_fresh: true })
    expect(await screen.findByText('Fresh bot')).toBeInTheDocument()
  })

  test('places a legal hold on the app and releases it', async () => {
    let placed: unknown = null
    let released = false
    let held = false
    common()
    server.use(
      http.get('/v1/portal/orgs/demo-org-001/legal-holds', () =>
        HttpResponse.json({
          holds: [],
          apps: held
            ? [{ org_id: 'demo-org-001', app_id: 'app-1', reason: 'Litigation', placed_by_user_id: 'u', placed_at: '2026-11-01T10:00:00Z' }]
            : [],
        }),
      ),
      http.put('/v1/portal/orgs/demo-org-001/legal-holds/apps/app-1', async ({ request }) => {
        placed = await request.json()
        held = true
        return HttpResponse.json({ org_id: 'demo-org-001', app_id: 'app-1', reason: 'Litigation', placed_by_user_id: 'u', placed_at: '2026-11-01T10:00:00Z' })
      }),
      http.delete('/v1/portal/orgs/demo-org-001/legal-holds/apps/app-1', () => {
        released = true
        held = false
        return new HttpResponse(null, { status: 204 })
      }),
    )

    page()
    await userEvent.type(await screen.findByRole('textbox', { name: /Reason for a legal hold/ }), 'Litigation')
    await userEvent.click(screen.getByRole('button', { name: 'Place legal hold' }))
    await expect.poll(() => placed).toEqual({ reason: 'Litigation' })
    await userEvent.click(await screen.findByRole('button', { name: 'Release legal hold' }))

    await expect.poll(() => released).toBe(true)
  })

  test('says so when the app is not there', async () => {
    server.use(http.get('/v1/portal/apps/app-1', () => HttpResponse.json({ detail: 'App not found' }, { status: 404 })))

    page()

    expect(await screen.findByText('App not found')).toBeInTheDocument()
  })
})
