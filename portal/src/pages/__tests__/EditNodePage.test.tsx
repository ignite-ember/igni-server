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

import type { PinnedNode, PinnedNodeUpdate } from '../../types'
import { EditNodePage } from '../EditNodePage'
import { renderWithProviders } from '../../test/render'
import { server } from '../../test/server'

const STORED: PinnedNode = {
  node_id: 'gpu-01',
  base_url: 'http://gpu-01.internal:8000/v1',
  models: ['igni-1.0-27b', 'legal-review'],
  max_concurrent: 64,
  multimodal: false,
  enabled: true,
  has_api_key: true,
  kind: 'openai',
  region: null,
  project: null,
  model_ids: {},
  auth: 'api_key',
  auth_settings: {},
  has_credential: false,
}

const VERTEX: PinnedNode = {
  ...STORED,
  base_url:
    'https://europe-west4-aiplatform.googleapis.com/v1/projects/acme-ai/locations/europe-west4/endpoints/openapi',
  models: ['gemini-pro'],
  has_api_key: false,
  kind: 'vertex_openai',
  region: 'europe-west4',
  project: 'acme-ai',
  model_ids: { 'gemini-pro': 'google/gemini-2.5-pro' },
  auth: 'ambient',
  auth_settings: {},
  has_credential: false,
}

const AZURE: PinnedNode = {
  ...STORED,
  base_url: 'https://acme.openai.azure.com/openai/v1',
  models: ['gpt-5'],
  has_api_key: false,
  kind: 'azure_openai',
  model_ids: { 'gpt-5': 'acme-gpt5' },
  auth: 'client_secret',
  auth_settings: { tenant_id: 'tenant-1', client_id: 'client-1' },
  has_credential: true,
}

function renderPage(nodeId = 'gpu-01') {
  return renderWithProviders(
    <Routes>
      <Route path="/inference/nodes/:nodeId/edit" element={<EditNodePage />} />
    </Routes>,
    { route: `/inference/nodes/${nodeId}/edit` },
  )
}

function serveStored(overrides: Partial<PinnedNode> = {}) {
  server.use(http.get('/v1/portal/inference/nodes/gpu-01', () => HttpResponse.json({ ...STORED, ...overrides })))
}

/** Every PATCH the page sends, as the update it is. */
function capturePatch(answer: PinnedNode = STORED): PinnedNodeUpdate[] {
  const sent: PinnedNodeUpdate[] = []
  server.use(
    http.patch('/v1/portal/inference/nodes/gpu-01', async ({ request }) => {
      sent.push((await request.json()) as PinnedNodeUpdate)
      return HttpResponse.json(answer)
    }),
  )
  return sent
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

    await user.clear(await screen.findByDisplayValue('64'))
    await user.type(screen.getByRole('spinbutton'), '8')

    expect(patched).toBe(false)
  })

  test('Save writes every field at once', async () => {
    serveStored()
    const sent = capturePatch()

    renderPage()
    const user = userEvent.setup()

    await user.click(await screen.findByRole('checkbox', { name: 'Accepts images' }))
    await user.click(screen.getByRole('button', { name: /Save node/i }))

    await waitFor(() => expect(sent).toHaveLength(1))
    expect(sent[0]).toEqual({
      base_url: 'http://gpu-01.internal:8000/v1',
      models: ['igni-1.0-27b', 'legal-review'],
      max_concurrent: 64,
      multimodal: true,
      enabled: true,
    })
  })

  test('a blank API key leaves the stored one alone', async () => {
    // The API treats an empty string as "clear it", and this page never
    // receives the key back — so sending it unconditionally would wipe
    // the credential on every edit of anything else.
    serveStored()
    const sent = capturePatch()

    renderPage()
    const user = userEvent.setup()

    await user.click(await screen.findByRole('button', { name: /Save node/i }))

    await waitFor(() => expect(sent).toHaveLength(1))
    expect(sent[0]).not.toHaveProperty('api_key')
  })

  test('a key that was typed is sent', async () => {
    serveStored()
    const sent = capturePatch()

    renderPage()
    const user = userEvent.setup()

    await user.type(await screen.findByLabelText('API key'), 'a-new-key')
    await user.click(screen.getByRole('button', { name: /Save node/i }))

    await waitFor(() => expect(sent).toHaveLength(1))
    expect(sent[0].api_key).toBe('a-new-key')
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

  describe('a cloud backend', () => {
    test('shows its method and settings, and says the secret is stored without showing it', async () => {
      serveStored(AZURE)
      renderPage()

      expect(await screen.findByDisplayValue('tenant-1')).toBeInTheDocument()
      expect(screen.getByDisplayValue('client-1')).toBeInTheDocument()
      expect(screen.getByRole('combobox', { name: /authentication/i })).toHaveValue('client_secret')
      expect(screen.getByPlaceholderText(/stored — leave blank to keep it/i)).toHaveValue('')
      expect(screen.getByDisplayValue('gpt-5 = acme-gpt5')).toBeInTheDocument()
    })

    test('saving without a new secret keeps the stored one', async () => {
      serveStored(AZURE)
      const sent = capturePatch(AZURE)
      renderPage()
      const user = userEvent.setup()

      await user.click(await screen.findByRole('button', { name: /save node/i }))

      await waitFor(() => expect(sent).toHaveLength(1))
      expect(sent[0]).toMatchObject({
        auth: 'client_secret',
        auth_settings: { tenant_id: 'tenant-1', client_id: 'client-1' },
        model_ids: { 'gpt-5': 'acme-gpt5' },
      })
      expect(sent[0]).not.toHaveProperty('credential')
    })

    test('switching to a managed identity sends it with only its own setting', async () => {
      serveStored(AZURE)
      const sent = capturePatch(AZURE)
      renderPage()
      const user = userEvent.setup()

      await user.selectOptions(
        await screen.findByRole('combobox', { name: /authentication/i }),
        await screen.findByRole('option', { name: /user-assigned managed identity/i }),
      )
      await user.type(screen.getByRole('textbox', { name: /client id/i }), 'uami-1')
      await user.click(screen.getByRole('button', { name: /save node/i }))

      await waitFor(() => expect(sent).toHaveLength(1))
      expect(sent[0]).toMatchObject({ auth: 'managed_identity', auth_settings: { client_id: 'uami-1' } })
    })

    test('a method whose secret is not stored yet is not saved without it', async () => {
      serveStored({ ...AZURE, auth: 'ambient', auth_settings: {}, has_credential: false })
      const sent = capturePatch(AZURE)
      renderPage()
      const user = userEvent.setup()

      await user.selectOptions(
        await screen.findByRole('combobox', { name: /authentication/i }),
        await screen.findByRole('option', { name: /client secret/i }),
      )
      await user.type(screen.getByRole('textbox', { name: /tenant id/i }), 't')
      await user.type(screen.getByRole('textbox', { name: /client id/i }), 'c')
      await user.click(screen.getByRole('button', { name: /save node/i }))

      expect(await screen.findByText(/needs every field/i)).toBeInTheDocument()
      expect(sent).toHaveLength(0)
    })
    test('a Vertex backend edits its project and region, and sends no address', async () => {
      serveStored(VERTEX)
      const sent = capturePatch(VERTEX)
      renderPage()
      const user = userEvent.setup()

      const region = await screen.findByRole('textbox', { name: /region/i })
      expect(screen.getByRole('textbox', { name: /project/i })).toHaveValue('acme-ai')
      await user.clear(region)
      await user.type(region, 'us-central1')
      await user.click(screen.getByRole('button', { name: /save node/i }))

      await waitFor(() => expect(sent).toHaveLength(1))
      expect(sent[0]).toEqual({
        project: 'acme-ai',
        region: 'us-central1',
        models: ['gemini-pro'],
        model_ids: { 'gemini-pro': 'google/gemini-2.5-pro' },
        max_concurrent: 64,
        multimodal: false,
        enabled: true,
        auth: 'ambient',
        auth_settings: {},
      } satisfies PinnedNodeUpdate)
    })

    test('a Vertex backend without its region is refused before the round trip', async () => {
      serveStored(VERTEX)
      const sent = capturePatch(VERTEX)
      renderPage()
      const user = userEvent.setup()

      await user.clear(await screen.findByRole('textbox', { name: /region/i }))
      await user.click(screen.getByRole('button', { name: /save node/i }))

      expect(await screen.findByText('A Vertex backend needs its project and region.')).toBeInTheDocument()
      expect(sent).toHaveLength(0)
    })
  })

  test('a node that will not load for another reason says so, not that it is defined elsewhere', async () => {
    server.use(
      http.get('/v1/portal/inference/nodes/gpu-01', () =>
        HttpResponse.json({ detail: 'The database is not answering.' }, { status: 500 }),
      ),
    )

    renderPage()

    expect(await screen.findByText('The node could not be loaded')).toBeInTheDocument()
    expect(screen.getByText('The database is not answering.')).toBeInTheDocument()
    expect(screen.queryByText(/cannot be edited here/i)).not.toBeInTheDocument()
  })
})
