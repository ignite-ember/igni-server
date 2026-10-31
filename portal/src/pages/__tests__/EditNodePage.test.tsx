/**
 * EditNodePage — the node's own page, reached by Edit.
 *
 * The fleet row used to be editable in place: two checkboxes that
 * PATCHed on change, with no Save. On a node with seven fields that made
 * a half-applied edit both possible and invisible — you could not see
 * what you were about to do before it was done.
 *
 * So the properties worth testing are the ones that shape says out loud:
 * nothing is written until Save, everything is written together, and a
 * node that cannot be saved from here says so rather than offering a
 * form that fails.
 */
import { http, HttpResponse } from 'msw'
import { screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { Route, Routes } from 'react-router-dom'
import { describe, expect, test } from 'vitest'

import { EditNodePage } from '../EditNodePage'
import { renderWithProviders } from '../../test/render'
import { server } from '../../test/server'

const STORED = {
  node_id: 'gpu-01',
  base_url: 'http://gpu-01.internal:8000/v1',
  models: ['igni-1.0-27b', 'legal-review'],
  max_concurrent: 64,
  pools: ['chat'],
  multimodal: false,
  enabled: true,
  has_api_key: true,
}

function renderPage(nodeId = 'gpu-01') {
  return renderWithProviders(
    <Routes>
      <Route path="/inference/nodes/:nodeId/edit" element={<EditNodePage />} />
    </Routes>,
    { route: `/inference/nodes/${nodeId}/edit` },
  )
}

function serveStored(overrides: Record<string, unknown> = {}) {
  server.use(
    http.get('/v1/portal/inference/nodes/gpu-01', () => HttpResponse.json({ ...STORED, ...overrides })),
  )
}

describe('The node edit page', () => {
  test('it loads the stored definition, not the live status', async () => {
    // `GET /inference` reports what the fleet is *doing* and carries no
    // `enabled` at all, so a form built from it would silently drop a
    // field on every save.
    serveStored()

    renderPage()

    expect(await screen.findByDisplayValue('http://gpu-01.internal:8000/v1')).toBeInTheDocument()
    expect(screen.getByDisplayValue('igni-1.0-27b, legal-review')).toBeInTheDocument()
    expect(screen.getByDisplayValue('64')).toBeInTheDocument()
    expect(screen.getByRole('checkbox', { name: 'Chat' })).toBeChecked()
    expect(screen.getByRole('checkbox', { name: 'CodeIndex' })).not.toBeChecked()
  })

  test('nothing is written until Save is pressed', async () => {
    // The property the whole page exists for.
    serveStored()
    let patched = false
    server.use(
      http.patch('/v1/portal/inference/nodes/gpu-01', () => {
        patched = true
        return HttpResponse.json({})
      }),
    )

    renderPage()
    const user = userEvent.setup()

    await user.click(await screen.findByRole('checkbox', { name: 'CodeIndex' }))
    await user.clear(screen.getByDisplayValue('64'))
    await user.type(screen.getByRole('spinbutton'), '8')

    expect(patched).toBe(false)
  })

  test('Save writes every field at once', async () => {
    serveStored()
    let body: Record<string, unknown> | null = null
    server.use(
      http.patch('/v1/portal/inference/nodes/gpu-01', async ({ request }) => {
        body = (await request.json()) as Record<string, unknown>
        return HttpResponse.json({})
      }),
    )

    renderPage()
    const user = userEvent.setup()

    await user.click(await screen.findByRole('checkbox', { name: 'CodeIndex' }))
    await user.click(screen.getByRole('checkbox', { name: 'Accepts images' }))
    await user.click(screen.getByRole('button', { name: /Save node/i }))

    await waitFor(() => expect(body).not.toBeNull())
    expect(body).toMatchObject({
      base_url: 'http://gpu-01.internal:8000/v1',
      models: ['igni-1.0-27b', 'legal-review'],
      max_concurrent: 64,
      pools: ['chat', 'codeindex'],
      multimodal: true,
      enabled: true,
    })
  })

  test('a blank API key leaves the stored one alone', async () => {
    // The API treats an empty string as "clear it", and this page never
    // receives the key back — so sending it unconditionally would wipe
    // the credential on every edit of anything else.
    serveStored()
    let body: Record<string, unknown> | null = null
    server.use(
      http.patch('/v1/portal/inference/nodes/gpu-01', async ({ request }) => {
        body = (await request.json()) as Record<string, unknown>
        return HttpResponse.json({})
      }),
    )

    renderPage()
    const user = userEvent.setup()

    await user.click(await screen.findByRole('button', { name: /Save node/i }))

    await waitFor(() => expect(body).not.toBeNull())
    expect(body).not.toHaveProperty('api_key')
  })

  test('a key that was typed is sent', async () => {
    serveStored()
    let body: Record<string, unknown> | null = null
    server.use(
      http.patch('/v1/portal/inference/nodes/gpu-01', async ({ request }) => {
        body = (await request.json()) as Record<string, unknown>
        return HttpResponse.json({})
      }),
    )

    renderPage()
    const user = userEvent.setup()

    await user.type(await screen.findByLabelText('API key'), 'a-new-key')
    await user.click(screen.getByRole('button', { name: /Save node/i }))

    await waitFor(() => expect(body).not.toBeNull())
    expect(body).toMatchObject({ api_key: 'a-new-key' })
  })

  test('a node serving nothing is refused before the round trip', async () => {
    // A node in no pool sits in the list taking no traffic, which looks
    // like a broken GPU rather than a choice. Stop serving is the thing
    // that expresses it, and the message says so.
    serveStored()
    let patched = false
    server.use(
      http.patch('/v1/portal/inference/nodes/gpu-01', () => {
        patched = true
        return HttpResponse.json({})
      }),
    )

    renderPage()
    const user = userEvent.setup()

    await user.click(await screen.findByRole('checkbox', { name: 'Chat' }))
    await user.click(screen.getByRole('button', { name: /Save node/i }))

    // Asserted on the sentence, not on "Stop serving" — the Enabled
    // caption points at the same control, so that phrase matches twice.
    expect(await screen.findByText(/serving no pool takes no traffic/i)).toBeInTheDocument()
    expect(patched).toBe(false)
  })

  test('an address without a scheme is refused before the round trip', async () => {
    serveStored()

    renderPage()
    const user = userEvent.setup()

    const address = await screen.findByDisplayValue('http://gpu-01.internal:8000/v1')
    await user.clear(address)
    await user.type(address, 'gpu-01.internal:8000')
    await user.click(screen.getByRole('button', { name: /Save node/i }))

    expect(await screen.findByText(/http:\/\/ or https:\/\//i)).toBeInTheDocument()
  })

  test('a refused save says why, with the reason the server gave', async () => {
    serveStored()
    server.use(
      http.patch('/v1/portal/inference/nodes/gpu-01', () =>
        HttpResponse.json({ detail: 'That address is already taken by a configured node' }, { status: 409 }),
      ),
    )

    renderPage()
    const user = userEvent.setup()

    await user.click(await screen.findByRole('button', { name: /Save node/i }))

    expect(await screen.findByText(/already taken by a configured node/i)).toBeInTheDocument()
  })

  test('a node defined elsewhere says so instead of showing a form', async () => {
    // The API answers 404 for a registered or statically-configured
    // node, because its definition lives in the cluster or the config
    // file. A form that cannot save is worse than no form.
    server.use(
      http.get('/v1/portal/inference/nodes/gpu-registered', () =>
        HttpResponse.json({ detail: 'not added here' }, { status: 404 }),
      ),
    )

    renderPage('gpu-registered')

    expect(await screen.findByText(/cannot be edited here/i)).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /Save node/i })).toBeNull()
  })

  test('the breadcrumb leads back to the fleet', async () => {
    serveStored()

    renderPage()

    const crumb = await screen.findByRole('link', { name: 'Inference' })
    expect(crumb).toHaveAttribute('href', '/inference')
  })
})
