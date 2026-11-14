/**
 * NodeDialog — adding a cloud backend.
 *
 * A cloud has no node to ask what it serves, and signs in its own way: the
 * dialog offers the methods the server's own table lists for the kind, asks
 * for exactly what the chosen one needs, and sends exactly that — no setting
 * of another method, no secret that was not typed.
 */
import { http, HttpResponse } from 'msw'
import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, test, vi } from 'vitest'

import { renderWithProviders } from '../../../test/render'
import { server } from '../../../test/server'
import type { PinnedNodeInput } from '../../../types'
import { NodeDialog } from '../NodeDialog'

function captureAdd(): PinnedNodeInput[] {
  const sent: PinnedNodeInput[] = []
  server.use(
    http.post('/v1/portal/inference/nodes', async ({ request }) => {
      sent.push((await request.json()) as PinnedNodeInput)
      return HttpResponse.json({}, { status: 201 })
    }),
  )
  return sent
}

async function openAs(kind: RegExp) {
  const user = userEvent.setup()
  renderWithProviders(<NodeDialog onClose={vi.fn()} />)
  await user.selectOptions(
    screen.getByRole('combobox', { name: /kind/i }),
    await screen.findByRole('option', { name: kind }),
  )
  return user
}

function submit() {
  return screen.getByRole('button', { name: /add node/i })
}

describe('Adding a cloud backend', () => {
  test('an Azure backend with a user-assigned managed identity sends its client ID and no secret', async () => {
    const sent = captureAdd()
    const user = await openAs(/azure openai/i)

    await user.type(screen.getByRole('textbox', { name: /endpoint/i }), 'https://acme.openai.azure.com/openai/v1')
    await user.type(screen.getByRole('textbox', { name: /^models/i }), 'gpt-5')
    await user.type(screen.getByRole('textbox', { name: /provider/i }), 'gpt-5 = acme-gpt5')
    await user.selectOptions(
      screen.getByRole('combobox', { name: /authentication/i }),
      screen.getByRole('option', { name: /user-assigned managed identity/i }),
    )
    expect(submit()).toBeDisabled()
    await user.type(screen.getByRole('textbox', { name: /client id/i }), 'uami-1')
    await user.type(screen.getByRole('textbox', { name: /^name/i }), 'azure-eu')
    await user.click(submit())

    await waitFor(() => expect(sent).toHaveLength(1))
    expect(sent[0]).toEqual({
      node_id: 'azure-eu',
      kind: 'azure_openai',
      base_url: 'https://acme.openai.azure.com/openai/v1',
      models: ['gpt-5'],
      model_ids: { 'gpt-5': 'acme-gpt5' },
      max_concurrent: 64,
      multimodal: false,
      auth: 'managed_identity',
      auth_settings: { client_id: 'uami-1' },
    } satisfies PinnedNodeInput)
  })

  test('Add says why it is disabled, and a provider-name line it cannot read keeps it so', async () => {
    const user = await openAs(/azure openai/i)

    expect(screen.getByText('The address has to start with http:// or https://')).toBeInTheDocument()
    await user.type(screen.getByRole('textbox', { name: /endpoint/i }), 'https://acme.openai.azure.com/openai/v1')
    await user.type(screen.getByRole('textbox', { name: /^models/i }), 'gpt-5')
    await user.type(screen.getByRole('textbox', { name: /^name/i }), 'azure-eu')
    await user.type(screen.getByRole('textbox', { name: /provider/i }), 'gpt-5: acme-gpt5')

    expect(
      screen.getByText('Each provider name is a line "name = provider-name", and this is not: gpt-5: acme-gpt5'),
    ).toBeInTheDocument()
    expect(submit()).toBeDisabled()
  })

  test("Azure offers every method the server lists, the server's own identity first", async () => {
    await openAs(/azure openai/i)

    const options = within(screen.getByRole('combobox', { name: /authentication/i })).getAllByRole('option')
    expect(options.map((o) => o.textContent)).toEqual([
      "The server's own identity",
      'User-assigned managed identity',
      'App registration (client secret)',
      'API key',
    ])
  })

  test('a Vertex backend asks for its project and region, and its key goes as the credential', async () => {
    const sent = captureAdd()
    const user = await openAs(/vertex/i)

    expect(screen.queryByRole('textbox', { name: /endpoint/i })).not.toBeInTheDocument()
    await user.type(screen.getByRole('textbox', { name: /project/i }), 'acme-ai')
    await user.type(screen.getByRole('textbox', { name: /region/i }), 'europe-west4')
    await user.type(screen.getByRole('textbox', { name: /^models/i }), 'gemini-pro')
    await user.selectOptions(
      screen.getByRole('combobox', { name: /authentication/i }),
      screen.getByRole('option', { name: /service-account key/i }),
    )
    expect(submit()).toBeDisabled()
    await user.click(screen.getByRole('textbox', { name: /service-account key/i }))
    await user.paste('{"type": "service_account"}')
    await user.type(screen.getByRole('textbox', { name: /^name/i }), 'vertex-eu')
    await user.click(submit())

    await waitFor(() => expect(sent).toHaveLength(1))
    expect(sent[0]).toEqual({
      node_id: 'vertex-eu',
      kind: 'vertex_openai',
      project: 'acme-ai',
      region: 'europe-west4',
      models: ['gemini-pro'],
      model_ids: {},
      max_concurrent: 64,
      multimodal: false,
      auth: 'service_account_key',
      auth_settings: {},
      credential: '{"type": "service_account"}',
    } satisfies PinnedNodeInput)
  })

  test("the server's own identity needs nothing more, and sends no secret", async () => {
    const sent = captureAdd()
    const user = await openAs(/vertex/i)

    await user.type(screen.getByRole('textbox', { name: /project/i }), 'acme-ai')
    await user.type(screen.getByRole('textbox', { name: /region/i }), 'global')
    await user.type(screen.getByRole('textbox', { name: /^models/i }), 'gemini-pro')
    await user.type(screen.getByRole('textbox', { name: /^name/i }), 'vertex-global')
    await user.click(submit())

    await waitFor(() => expect(sent).toHaveLength(1))
    expect(sent[0]).toEqual({
      node_id: 'vertex-global',
      kind: 'vertex_openai',
      project: 'acme-ai',
      region: 'global',
      models: ['gemini-pro'],
      model_ids: {},
      max_concurrent: 64,
      multimodal: false,
      auth: 'ambient',
      auth_settings: {},
    } satisfies PinnedNodeInput)
  })

  test('a methods table that fails to load is said, and only a GPU node is offered', async () => {
    server.use(http.get('/v1/portal/inference/auth-methods', () => HttpResponse.json({}, { status: 500 })))
    renderWithProviders(<NodeDialog onClose={vi.fn()} />)

    expect(await screen.findByText(/cloud providers are unavailable/i)).toBeInTheDocument()
    const kinds = within(screen.getByRole('combobox', { name: /kind/i })).getAllByRole('option')
    expect(kinds.map((o) => o.textContent)).toEqual(['GPU node or OpenAI-compatible API'])
  })
})
