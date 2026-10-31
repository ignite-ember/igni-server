/**
 * One service and the whole of its extension: what a super admin reads
 * before approving it. Driven by the real Artifacts and CodeIndex
 * manifests, and by a manifest with most of its keys missing — the
 * service is the party being vetted, so the page cannot trust it to send
 * a complete one.
 */
import { describe, expect, test } from 'vitest'
import { http, HttpResponse } from 'msw'
import userEvent from '@testing-library/user-event'
import { screen, waitFor, within } from '@testing-library/react'
import { Route, Routes } from 'react-router-dom'
import { renderWithProviders } from '../../test/render'
import { server } from '../../test/server'
import { ARTIFACTS_EXTENSION, CODEINDEX_EXTENSION } from '../../test/fixtures/serviceExtensions'
import { ServiceDetailPage } from '../ServiceDetailPage'
import type { RegisteredClientDetail, ServiceExtensionDetail } from '../../types'

const ME = {
  user_id: 'boss',
  email: 'boss@acmecorp.io',
  full_name: 'The Boss',
  created_at: '2026-01-01T00:00:00Z',
  organizations: [],
  is_super_admin: true,
}

function summaryOf(extension: ServiceExtensionDetail) {
  const { extension_id, version, name, description, published_at } = extension
  return { extension_id, version, name, description, published_at }
}

const CODEINDEX: RegisteredClientDetail = {
  client_id: 'client-codeindex',
  kind: 'service',
  name: 'CodeIndex',
  base_url: 'https://codeindex.acmecorp.io',
  redirect_uris: ['https://codeindex.acmecorp.io/auth/callback'],
  thumbprint: 'NzbLsXh8uDCcd-6MNwXF4W_7noWXFZAfHkxZsRGC9Xs',
  state: 'pending',
  created_at: '2026-08-01T10:00:00Z',
  decided_at: null,
  extension: summaryOf(CODEINDEX_EXTENSION),
  extension_detail: CODEINDEX_EXTENSION,
}

const ARTIFACTS: RegisteredClientDetail = {
  client_id: 'client-artifacts',
  kind: 'service',
  name: 'Artifacts',
  base_url: 'https://artifacts.acmecorp.io',
  redirect_uris: [],
  thumbprint: 'Zr9wq0ExU1t4mN8bB2xYv3cD5fG7hJ9kL1pQ3sT5uW7',
  state: 'approved',
  created_at: '2026-09-01T10:00:00Z',
  decided_at: '2026-09-01T10:05:00Z',
  extension: summaryOf(ARTIFACTS_EXTENSION),
  extension_detail: ARTIFACTS_EXTENSION,
}

function serve(detail: RegisteredClientDetail) {
  server.use(
    http.get('/v1/portal/me', () => HttpResponse.json(ME)),
    http.get('/v1/portal/clients/:clientId', ({ params }) =>
      params.clientId === detail.client_id
        ? HttpResponse.json(detail)
        : HttpResponse.json({ detail: 'No such client' }, { status: 404 }),
    ),
  )
}

function renderAt(clientId: string) {
  return renderWithProviders(
    <Routes>
      <Route path="/services" element={<p>The list of services</p>} />
      <Route path="/services/:clientId" element={<ServiceDetailPage />} />
    </Routes>,
    { route: `/services/${clientId}` },
  )
}

function section(name: string): HTMLElement {
  return screen.getByRole('region', { name })
}

/** Each item's text in a named list inside `container`. */
function list(container: HTMLElement, name: string): string[] {
  return Array.from(within(container).getByRole('list', { name }).querySelectorAll(':scope > li'), (item) =>
    item.textContent ?? '',
  )
}

describe('ServiceDetailPage', () => {
  test('puts a held update, and what it adds, right after the thumbprint', async () => {
    serve({
      ...ARTIFACTS,
      held_version: '0.2.0',
      held_update: {
        client_id: ARTIFACTS.client_id,
        service: 'Artifacts',
        extension_id: 'igni.artifacts',
        name: 'Artifacts',
        approved_version: '0.1.0',
        version: '0.2.0',
        sha256: 'b'.repeat(64),
        uploaded_at: '2026-10-30T10:00:00Z',
        comparison: {
          wider: true,
          added: [{ kind: 'secret', label: 'Secret', item: 'AWS_KEY', was: null }],
        },
      },
    })

    renderAt('client-artifacts')

    const held = await screen.findByRole('region', { name: 'Update asks for more' })
    expect(list(held, 'What it adds')).toEqual(['Secret: AWS_KEY'])
    expect(within(held).getByRole('button', { name: 'Approve version 0.2.0 of Artifacts' })).toBeInTheDocument()
    expect(within(held).getByRole('button', { name: 'Reject version 0.2.0 of Artifacts' })).toBeInTheDocument()
    expect(screen.getByText('Update asks for more: 0.2.0')).toBeInTheDocument()
    const regions = screen.getAllByRole('region').map((region) => region.getAttribute('aria-labelledby'))
    expect(regions.slice(0, 2)).toEqual(['service-thumbprint', 'held-update'])
  })

  test('shows no held update when there is none', async () => {
    serve(ARTIFACTS)

    renderAt('client-artifacts')

    await screen.findByRole('region', { name: 'What it is' })
    expect(screen.queryByRole('region', { name: 'Update asks for more' })).toBeNull()
  })

  test('puts the thumbprint, and how to check it, before anything else', async () => {
    serve(CODEINDEX)

    renderAt('client-codeindex')

    const thumbprint = await screen.findByRole('region', { name: 'Key thumbprint' })
    expect(within(thumbprint).getByText(CODEINDEX.thumbprint).tagName).toBe('CODE')
    expect(within(thumbprint).getByText(/character for character/i)).toBeInTheDocument()
    expect(within(thumbprint).getByText('https://codeindex.acmecorp.io/auth/callback')).toBeInTheDocument()
    expect(screen.getByText('Waiting for approval')).toBeInTheDocument()

    // Ahead of every section about the extension, in reading order.
    const regions = screen.getAllByRole('region').map((region) => region.getAttribute('aria-labelledby'))
    expect(regions[0]).toBe('service-thumbprint')
  })

  test('says what the extension is', async () => {
    serve(ARTIFACTS)

    renderAt('client-artifacts')

    const what = await screen.findByRole('region', { name: 'What it is' })
    const facts = within(what)
    expect(facts.getByText('igni.artifacts')).toBeInTheDocument()
    expect(facts.getByText('0.1.0')).toBeInTheDocument()
    expect(facts.getByText('Ignite Ember')).toBeInTheDocument()
    expect(facts.getByText(ARTIFACTS_EXTENSION.description)).toBeInTheDocument()
    expect(facts.getByText(ARTIFACTS_EXTENSION.sha256)).toBeInTheDocument()
    expect(facts.getByText('unsigned, as its author labels it')).toBeInTheDocument()
    expect(facts.getByText('>=1.0 <2')).toBeInTheDocument()
    expect(facts.getByText(/run from a source checkout/i)).toBeInTheDocument()
    expect(facts.getByText('igni-artifacts-extension')).toBeInTheDocument()
  })

  test("lists what it adds to igni's UI, settings with their defaults", async () => {
    serve(ARTIFACTS)

    renderAt('client-artifacts')

    const adds = await screen.findByRole('region', { name: 'What it adds to igni' })
    expect(list(adds, 'Pages')).toEqual(['Artifactsigni.artifactsIn the sidebar'])
    expect(list(adds, 'Menu entries')).toEqual(['Artifacts /artifacts — documents the agent writes and cites'])
    expect(list(adds, 'Commands')).toEqual(['/artifacts — project documents'])
    expect(list(adds, 'Status indicators')).toEqual([
      'Artifactsigni.artifacts.waitingOpens igni.artifacts; refreshes every 30 seconds',
    ])
    expect(list(adds, 'Settings')).toEqual([
      'Artifacts serviceserviceUrlstringDefault: ""',
      'Refuse a document with no cited sourcerequireSourcesbooleanDefault: true',
    ])
  })

  test('lists what it may touch', async () => {
    serve(ARTIFACTS)

    renderAt('client-artifacts')

    const touches = await screen.findByRole('region', { name: 'What it may touch' })
    expect(within(touches).getByText("Reads people's projects")).toBeInTheDocument()
    // `workspacePaths: []` is the whole project, as the host reads it.
    expect(within(touches).getByText('The whole project')).toBeInTheDocument()
    expect(within(touches).getByRole('heading', { name: 'Network egress' }).parentElement).toHaveTextContent(
      'None',
    )
    // What egress governs, said where it is read: the page, not the process.
    expect(within(touches).getByText(/its process on each machine is not limited by it/i)).toBeInTheDocument()
    expect(list(touches, 'Secrets')).toEqual(['artifacts-service-token'])
    expect(list(touches, 'Tools for the agent')).toHaveLength(19)
    // Each with what it says it is: igni's permission modes decide on it.
    expect(list(touches, 'Tools for the agent')).toContain('igni.artifacts.artifact_cypherread-only, local, idempotent')
    expect(list(touches, 'Tools for the agent')).toContain('igni.artifacts.artifact_deletedestructive, local, idempotent')
    expect(list(touches, 'Tools for the agent')).toContain('igni.artifacts.artifact_createchanges things, local')
    expect(within(touches).getByText('Only the agents in its own plugin may call these.')).toBeInTheDocument()
    expect(list(touches, 'Events it is told about')).toEqual(['workspace.head-changed'])
    expect(list(touches, 'What it may reach in igni')).toEqual([
      "linksOpens https links in the person's browser.",
      "decideAsks igni's local decision model about text it sends. Runs on the machine; nothing leaves it.",
    ])
    expect(list(touches, "igni's hook events it answers")).toEqual([
      'PlanSubmitAsked before it happens: may stop it, or change it.',
      'SessionStartTold when it happens; nothing waits on it.',
    ])
    // Its own service's methods: nothing of igni's.
    expect(list(touches, 'Calls its pages may make')).toEqual(['ext.igni.artifacts.*'])

    expect(within(section('What it ships')).getByRole('heading', { name: 'Assets' }).parentElement).toHaveTextContent(
      'None',
    )
  })

  test('lists what it ships, pinned, for each platform', async () => {
    serve(CODEINDEX)

    renderAt('client-codeindex')

    const ships = await screen.findByRole('region', { name: 'What it ships' })
    const assets = list(ships, 'Assets')
    expect(assets).toHaveLength(6)
    expect(assets[0]).toContain('neo4j')
    expect(assets[0]).toContain('Every platform')
    expect(assets[0]).toContain('neo4j-community-5.26.0-unix.tar.gz')
    expect(assets[0]).toContain('151.5 MB, unpacked on install')
    expect(assets[0]).toContain('ad8ac3398606145502b8f489530bbd39333707ae4668156af16b6086b5b037d7')
    expect(assets[0]).toContain('https://dist.neo4j.org/neo4j-community-5.26.0-unix.tar.gz')
    expect(assets.filter((row) => row.startsWith('jre')).map((row) => row.split('OpenJDK')[0])).toEqual([
      'jremacOS (Apple silicon)',
      'jreLinux x86_64',
      'jreWindows x86_64',
    ])
    expect(assets[4]).toContain('model.onnx')
    expect(assets[4]).not.toContain('unpacked')
  })

  test('marks every prompt it overrides, and what it edits', async () => {
    serve(CODEINDEX)

    renderAt('client-codeindex')

    const prompt = await screen.findByRole('region', { name: "What it changes in igni's prompts" })
    const overrides = within(prompt).getByRole('list', { name: 'Prompts it overrides' })
    const explorer = within(overrides)
      .getAllByRole('listitem')
      .find((item) => within(item).queryByText('agent:explorer') !== null)
    expect(explorer).toBeDefined()
    expect(within(explorer!).getByText('Its own the explorer agent')).toBeInTheDocument()
    expect(within(explorer!).getByText("Takes no one else's edits")).toBeInTheDocument()
    expect(within(explorer!).getByText(/tracing execution paths/)).toBeInTheDocument()
    expect(explorer).toHaveClass('service-replaces')
    expect(list(prompt, 'Prompts it overrides')).toHaveLength(3)

    const [planMode, architect] = list(prompt, 'Prompts it edits')
    expect(planMode).toContain('It adds the section code_intelligence at the end')
    expect(architect).toContain('It replaces the section frontmatter')
    expect(architect).toContain('It adds the section index_stores after intro')
    expect(list(prompt, 'Prompt fragments')).toEqual(['CODEINDEX_GRAPH_SCHEMA — from its tool graph_schema'])

    const plugin = section('Its plugin')
    expect(list(plugin, 'Skills')).toEqual(['cypher-recipesWorked Cypher queries for the per-commit graph.'])

    // Said once more, above everything, because it changes igni for everybody.
    expect(screen.getByText('It takes over part of igni')).toBeInTheDocument()
    expect(screen.getByText(/people get its own igni's main prompt; own the data-architect agent/)).toBeInTheDocument()
  })

  test("a plugin's own agent is a new agent, and its edits say which sections they replace", async () => {
    serve(ARTIFACTS)

    renderAt('client-artifacts')

    const plugin = await screen.findByRole('region', { name: 'Its plugin' })
    expect(list(plugin, 'Agents')[0]).toContain('archivistNew agent')
    expect(within(plugin).getByRole('heading', { name: 'Skills' }).parentElement).toHaveTextContent('None')

    const prompt = section("What it changes in igni's prompts")
    const [main] = list(prompt, 'Prompts it edits')
    for (const replaced of ['project_knowledge', 'durable_knowledge', 'reading', 'project_documents']) {
      expect(main).toContain(`It replaces the section ${replaced}`)
    }
    expect(within(prompt).getByRole('heading', { name: 'Prompts it overrides' }).parentElement).toHaveTextContent(
      'None',
    )
    expect(
      screen.getByText(/people get its versions of 4 sections of igni's prompts \(main#project_knowledge/),
    ).toBeInTheDocument()

    const asSent = section('The manifest as sent')
    expect(asSent.querySelector('pre')?.textContent).toBe(JSON.stringify(ARTIFACTS_EXTENSION.manifest, null, 2))
  })

  test('an extension that only adds sections takes nothing over', async () => {
    serve({
      ...ARTIFACTS,
      extension_detail: {
        ...ARTIFACTS_EXTENSION,
        manifest: {
          ...ARTIFACTS_EXTENSION.manifest,
          contributes: {
            prompt: {
              edits: [
                { target: 'main', insert: 'review', after: 'workflow', source: 'file', from: 'prompts/review.md' },
                { target: 'plan_mode', append: 'tail', source: 'file', from: 'prompts/tail.md', when: 'indexed' },
              ],
            },
          },
        },
      },
    })

    renderAt('client-artifacts')

    const prompt = await screen.findByRole('region', { name: "What it changes in igni's prompts" })
    expect(list(prompt, 'Prompts it edits')).toEqual([
      'mainIt adds the section review after workflow — from file prompts/review.md',
      'plan_modeIt adds the section tail at the end — from file prompts/tail.md — only while indexed holds',
    ])
    expect(screen.queryByText('It takes over part of igni')).not.toBeInTheDocument()
  })

  test('shows where it collides, who has each by priority, and what it loses', async () => {
    serve(CODEINDEX)

    renderAt('client-codeindex')

    const collisions = await screen.findByRole('region', { name: 'Where it collides with other extensions' })
    const [lost, won] = list(collisions, 'Collisions')
    expect(lost).toContain('prompt:agent:explorer')
    expect(lost).toContain('acme.scout has it by priority — igni.codeindex loses it')
    expect(won).toContain('igni.codeindex has it by priority — acme.search loses it')
    expect(within(collisions).getByText('It loses some of what it wants')).toBeInTheDocument()
    expect(within(collisions).getByText(/lower priority than acme.scout/)).toBeInTheDocument()
    // Nothing to decide here, and nothing held back.
    expect(within(collisions).queryByRole('button')).not.toBeInTheDocument()
    expect(within(collisions).queryByText(/held back/)).not.toBeInTheDocument()
    // Not a collision: its override of main leaves out another extension's edit, and says so.
    expect(within(collisions).getByText("Its override leaves out other extensions' edits")).toBeInTheDocument()
    expect(within(collisions).getByText('acme.docs: prompt:main#reading')).toBeInTheDocument()
  })

  test("says when it declares one of igni's own commands", async () => {
    serve({
      ...CODEINDEX,
      extension_detail: {
        ...CODEINDEX_EXTENSION,
        review: { ...CODEINDEX_EXTENSION.review!, igni_commands: ['/clear'] },
      },
    })

    renderAt('client-codeindex')

    const collisions = await screen.findByRole('region', { name: 'Where it collides with other extensions' })
    expect(within(collisions).getByText("It declares igni's own commands")).toBeInTheDocument()
    expect(within(collisions).getByText(/igni answers \/clear itself/)).toBeInTheDocument()
  })

  test('an extension that collides with nothing says so', async () => {
    serve(ARTIFACTS)

    renderAt('client-artifacts')

    const collisions = await screen.findByRole('region', { name: 'Where it collides with other extensions' })
    expect(within(collisions).getByRole('heading', { name: 'Collisions' }).parentElement).toHaveTextContent('None')
    expect(within(collisions).queryByText('It loses some of what it wants')).not.toBeInTheDocument()
  })

  test('copes with a manifest missing nearly everything', async () => {
    serve({
      ...CODEINDEX,
      extension_detail: {
        ...CODEINDEX_EXTENSION,
        manifest: {
          id: 'acme.sparse',
          contributes: { pages: [{ label: 'no kind' }, 'not an object'], settings: [{ key: 'colour' }] },
          permissions: { tools: 'not a list', rpc: ['igni.workspace.read'] },
          assets: [{ file: 'no name' }, 'not an object', { name: 'model', file: 'model.onnx' }],
        },
        plugin: {},
      },
    })

    renderAt('client-codeindex')

    const what = await screen.findByRole('region', { name: 'What it is' })
    // Unstated, not blank and not a crash.
    expect(within(what).getAllByText('Not stated').length).toBeGreaterThanOrEqual(4)

    const adds = section('What it adds to igni')
    for (const part of ['Pages', 'Menu entries', 'Commands', 'Status indicators']) {
      expect(within(adds).getByRole('heading', { name: part }).parentElement).toHaveTextContent('None')
    }
    expect(list(adds, 'Settings')).toEqual(['colourcolourDefault: none'])

    const touches = section('What it may touch')
    expect(within(touches).getByText("None — it cannot see people's projects")).toBeInTheDocument()
    expect(within(touches).getByRole('heading', { name: 'Tools for the agent' }).parentElement).toHaveTextContent(
      'None',
    )
    // A method outside its own `ext.<id>.` namespace reaches igni's data.
    expect(list(touches, 'Calls its pages may make')).toEqual(["igni.workspace.readigni's own data"])

    expect(list(section('What it ships'), 'Assets')).toEqual(['modelEvery platformmodel.onnxsize not statedSHA-256 not stated'])
    const plugin = section('Its plugin')
    expect(within(plugin).getByRole('heading', { name: 'Agents' }).parentElement).toHaveTextContent('None')
  })

  test('says when the service has not uploaded its extension', async () => {
    serve({ ...CODEINDEX, extension: null, extension_detail: null })

    renderAt('client-codeindex')

    expect(await screen.findByText('Has not uploaded its extension yet')).toBeInTheDocument()
    expect(screen.queryByRole('region', { name: 'What it is' })).not.toBeInTheDocument()
    // The thumbprint and the decision are still here.
    expect(screen.getByText(CODEINDEX.thumbprint)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Approve CodeIndex' })).toBeInTheDocument()
  })

  test('approves from here', async () => {
    serve(CODEINDEX)
    let approved: string | null = null
    server.use(
      http.post('/v1/portal/clients/:clientId/approve', ({ params }) => {
        approved = String(params.clientId)
        return HttpResponse.json({ ...CODEINDEX, state: 'approved' })
      }),
    )

    renderAt('client-codeindex')
    await userEvent.click(await screen.findByRole('button', { name: 'Approve CodeIndex' }))

    await waitFor(() => expect(approved).toBe('client-codeindex'))
  })

  test("shows the server's reason when another approved service ships the same extension", async () => {
    serve(CODEINDEX)
    server.use(
      http.post('/v1/portal/clients/:clientId/approve', () =>
        HttpResponse.json({ detail: 'another approved service already ships igni.codeindex' }, { status: 409 }),
      ),
    )

    renderAt('client-codeindex')
    await userEvent.click(await screen.findByRole('button', { name: 'Approve CodeIndex' }))

    expect(await screen.findByText('Not changed')).toBeInTheDocument()
    expect(screen.getByText('another approved service already ships igni.codeindex')).toBeInTheDocument()
  })

  test('revokes from here once the confirmation is accepted', async () => {
    serve(ARTIFACTS)
    let revoked: string | null = null
    server.use(
      http.post('/v1/portal/clients/:clientId/revoke', ({ params }) => {
        revoked = String(params.clientId)
        return HttpResponse.json({ ...ARTIFACTS, state: 'revoked' })
      }),
    )

    const user = userEvent.setup()
    renderAt('client-artifacts')
    await user.click(await screen.findByRole('button', { name: 'Revoke Artifacts' }))
    const dialog = await screen.findByRole('alertdialog')
    expect(within(dialog).getByText(/remove its extension/i)).toBeInTheDocument()
    expect(revoked).toBeNull()
    await user.click(within(dialog).getByRole('button', { name: 'Revoke' }))

    await waitFor(() => expect(revoked).toBe('client-artifacts'))
  })

  test('goes back to the list once removed', async () => {
    serve(CODEINDEX)
    server.use(http.delete('/v1/portal/clients/:clientId', () => new HttpResponse(null, { status: 204 })))

    const user = userEvent.setup()
    renderAt('client-codeindex')
    await user.click(await screen.findByRole('button', { name: 'Remove CodeIndex' }))
    await user.click(within(await screen.findByRole('alertdialog')).getByRole('button', { name: 'Remove' }))

    expect(await screen.findByText('The list of services')).toBeInTheDocument()
  })

  test('says so when there is no such service', async () => {
    serve(CODEINDEX)

    renderAt('client-gone')

    expect(await screen.findByText('No such service')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Back to services' })).toHaveAttribute('href', '/services')
  })

  test('is not for anybody else', async () => {
    server.use(http.get('/v1/portal/me', () => HttpResponse.json({ ...ME, is_super_admin: false })))

    renderAt('client-codeindex')

    expect(await screen.findByText('Super admins only')).toBeInTheDocument()
  })
})
