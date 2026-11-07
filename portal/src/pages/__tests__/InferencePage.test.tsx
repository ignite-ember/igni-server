/**
 * InferencePage — the states an operator actually lands in.
 *
 * The page exists to answer "do we need another GPU", so the tests that
 * matter are the ones where a wrong answer costs something: a stale node
 * that looks healthy, a fleet with no nodes, and an empty registered
 * list that could mean either "nobody registered" or "registration is
 * switched off".
 */
import { beforeEach, describe, expect, test } from 'vitest'
import { http, HttpResponse } from 'msw'
import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { renderWithProviders } from '../../test/render'
import { server } from '../../test/server'
import { InferencePage } from '../InferencePage'

function node(overrides: Record<string, unknown> = {}) {
  return {
    id: 'gpu-01',
    base_url: 'http://gpu-01.internal:8000/v1',
    models: ['igni-1.0-27b'],
    multimodal: false,
    source: 'static',
    in_flight: 0,
    max_concurrent: 64,
    utilization: 0,
    last_seen_seconds_ago: null,
    registered_at: null,
    health: { state: 'healthy', detail: null, latency_ms: 12 },
    metrics: null,
    drain: null,
    ...overrides,
  }
}

function status(overrides: Record<string, unknown> = {}) {
  return {
    nodes: [node()], total_in_flight: 0, total_capacity: 64, utilization: 0,
    registration_enabled: true,
    heartbeat_interval_seconds: 60,
    ...overrides,
  }
}

function respondWith(payload: object, init?: { status: number }, me: { is_super_admin: boolean } = { is_super_admin: true }) {
  server.use(
    http.get('/v1/portal/inference', () => HttpResponse.json(payload, init)),
    // Changing the fleet is a super admin's (SER-32); most tests here are
    // about the controls, so they sign in as one.
    http.get('/v1/portal/me', () =>
      HttpResponse.json({ user_id: 'u-1', email: 'admin@test.com', organizations: [{ id: 'o-1' }], ...me }),
    ),
  )
}

describe('The node list', () => {
  /**
   * A row is a disclosure, not a table cell. It carries only what you
   * scan for — up or down, how loaded, anything queued — because the
   * table that held everything was seven columns wide and put its most
   * interesting one off the right edge of the window.
   */

  async function expand(nodeId: string) {
    // A native <summary>, reached through the node's name. Scoped to the
    // list because the "Needs attention" card names unwell nodes too, so
    // a bare text lookup can match twice.
    const user = userEvent.setup()
    const target = await waitFor(() => {
      const rows = [...document.querySelectorAll('.inference-node-summary')]
      const row = rows.find((el) => el.textContent?.includes(nodeId))
      if (!row) throw new Error(`no row for ${nodeId}`)
      return row
    })
    await user.click(target)
    return user
  }

  test('a collapsed row shows the name and the load, and nothing else', async () => {
    respondWith(
      status({
        nodes: [node({ in_flight: 12 })],
            total_in_flight: 12,
            total_capacity: 64,
            utilization: 12 / 64,
      }),
    )

    renderWithProviders(<InferencePage />)

    expect(await screen.findByText('gpu-01')).toBeInTheDocument()
    // A bar and its percentage; the exact counts are in the panel. Scoped
    // to the row, since the capacity card shows a percentage too.
    expect(screen.getByRole('progressbar', { name: '12 of 64 in flight' })).toBeInTheDocument()
    const row = document.querySelector('.inference-node-summary')!
    expect(within(row as HTMLElement).getByText('19%')).toBeInTheDocument()
    // Detail is behind the disclosure.
    expect(screen.queryByText('http://gpu-01.internal:8000/v1')).not.toBeInTheDocument()
  })

  test('expanding reveals the address and the model list', async () => {
    respondWith(status({ nodes: [node({ models: ['base-27b', 'sql-expert'] })], total_in_flight: 0, total_capacity: 64, utilization: 0 }))

    renderWithProviders(<InferencePage />)
    await expand('gpu-01')

    expect(await screen.findByText('http://gpu-01.internal:8000/v1')).toBeInTheDocument()
    // Each model is its own Token chip — the base model and then the
    // adapters — rather than a comma-joined string.
    expect(screen.getByText('base-27b')).toBeInTheDocument()
    expect(screen.getByText('sql-expert')).toBeInTheDocument()
  })

  test('a queue stays visible while collapsed', async () => {
    // The one number that means "go and look". Burying it behind a click
    // would defeat the point of scraping it.
    respondWith(
      status({
        nodes: [
              node({
                in_flight: 4,
                metrics: { running: 64, waiting: 5, kv_cache_percent: 97.2, prefix_cache_hit_percent: 81.2, preemptions: 4 },
                health: { state: 'degraded', detail: '5 request(s) queued on the node itself', latency_ms: 11 },
              }),
            ],
            total_in_flight: 4,
            total_capacity: 64,
            utilization: 0.06,
      }),
    )

    renderWithProviders(<InferencePage />)

    expect(await screen.findByText('5 queued')).toBeInTheDocument()
    // The rest of the gauges are not.
    expect(screen.queryByText(/KV cache/i)).not.toBeInTheDocument()
  })

  test('the bars are actually filled, and coloured by a real token', async () => {
    // The regression this exists for: ProgressBar splits `bg` on the dot
    // and builds `var(--bgColor-<role>-<variant>)` itself. Handing it a
    // finished `var(...)` yields nested nonsense that the browser drops,
    // and every bar renders grey with no error anywhere.
    respondWith(
      status({
        nodes: [node({ in_flight: 8, metrics: { running: 8, waiting: 0, kv_cache_percent: 95, prefix_cache_hit_percent: 90, preemptions: 0 } })],
            total_in_flight: 8,
            total_capacity: 64,
            utilization: 0.125,
      }),
    )

    renderWithProviders(<InferencePage />)
    await expand('gpu-01')

    // The label lands on the filled item itself, which is the element
    // carrying the custom properties.
    const routed = await screen.findByRole('progressbar', { name: 'Routed by igni: 8 / 64' })
    expect(routed.style.getPropertyValue('--progress-width')).toBe('12.5%')
    expect(routed.style.getPropertyValue('--progress-bg')).toBe('var(--bgColor-success-emphasis)')

    // A nearly-full KV cache turns the bar red rather than leaving it
    // the default green.
    const cache = screen.getByRole('progressbar', { name: 'KV cache: 95%' })
    expect(cache.style.getPropertyValue('--progress-bg')).toBe('var(--bgColor-danger-emphasis)')
  })

  test('expanding shows every gauge the node reported', async () => {
    respondWith(
      status({
        nodes: [node({ in_flight: 4, metrics: { running: 64, waiting: 5, kv_cache_percent: 97.2, prefix_cache_hit_percent: 81.2, preemptions: 4 } })],
            total_in_flight: 4,
            total_capacity: 64,
            utilization: 0.06,
      }),
    )

    renderWithProviders(<InferencePage />)
    await expand('gpu-01')

    // Bars, not a wall of prose: each gauge is labelled and carries its
    // own readout.
    expect(await screen.findByLabelText('Routed by igni: 4 / 64')).toBeInTheDocument()
    expect(screen.getByLabelText('Running on the node: 64 / 64')).toBeInTheDocument()
    expect(screen.getByLabelText('KV cache: 97.2%')).toBeInTheDocument()
    expect(screen.getByLabelText('Prefix cache hits: 81.2%')).toBeInTheDocument()
    expect(screen.getByText('5 requests')).toBeInTheDocument()
    expect(screen.getByText('4 since start')).toBeInTheDocument()
  })

  test('a disagreement with our own count is called out in the detail', async () => {
    // Four leases issued, sixty-four sequences decoding: something else
    // is using this GPU, and only the node knows.
    respondWith(
      status({
        nodes: [node({ in_flight: 4, metrics: { running: 64, waiting: 0, kv_cache_percent: 80, prefix_cache_hit_percent: 40, preemptions: 0 } })],
            total_in_flight: 4,
            total_capacity: 64,
            utilization: 0.06,
      }),
    )

    renderWithProviders(<InferencePage />)
    await expand('gpu-01')

    expect(await screen.findByText(/something else is using this GPU/i)).toBeInTheDocument()
  })

  test('what a node serves can be changed after it is added', async () => {
    // It could only be set once, at creation, inside a dialog — and even
    // there it was hidden until a probe succeeded. "This node should
    // also do indexing" is the change people actually make.
    //
    // It moved from a checkbox on this row to the node's own page: two
    // checkboxes that saved on click made a half-applied edit both
    // possible and invisible on a node with seven fields. The property
    // asserted is unchanged — the change is reachable after creation —
    // and `EditNodePage.test.tsx` covers the writing of it.
    respondWith(
      status({
        nodes: [node({ id: 'gpu-hand', source: 'pinned' })],
            total_in_flight: 0,
            total_capacity: 64,
            utilization: 0,
      }),
    )

    renderWithProviders(<InferencePage />)
    await expand('gpu-hand')

    expect(await screen.findByRole('link', { name: /Edit/i })).toHaveAttribute(
      'href',
      '/inference/nodes/gpu-hand/edit',
    )
  })

  test('a node defined elsewhere says where to change it', async () => {
    // A configured node changes by redeploy and a registered one by
    // whatever started the cluster, so offering a form would be a lie.
    respondWith(status({ nodes: [node({ source: 'static' })] }))

    renderWithProviders(<InferencePage />)
    await expand('gpu-01')

    expect(await screen.findByText(/change it there and redeploy/i)).toBeInTheDocument()
  })

  test('removing a node asks first', async () => {
    // It takes the node out of rotation for everyone, and the same panel
    // offers a checkbox that only changes what it serves — two very
    // different outcomes a click apart.
    let deleted = false
    server.use(
      http.delete('/v1/portal/inference/nodes/gpu-hand', () => {
        deleted = true
        return new HttpResponse(null, { status: 204 })
      }),
    )
    respondWith(
      status({
        nodes: [node({ id: 'gpu-hand', source: 'pinned', in_flight: 3 })],
            total_in_flight: 3,
            total_capacity: 64,
            utilization: 0.05,
      }),
    )

    renderWithProviders(<InferencePage />)
    const user = await expand('gpu-hand')
    await user.click(await screen.findByRole('button', { name: /remove node/i }))

    // Nothing has happened yet.
    expect(deleted).toBe(false)
    expect(await screen.findByText(/Remove gpu-hand\?/)).toBeInTheDocument()
    // It says what is at stake, including work already in flight.
    expect(screen.getByText(/3 requests already in flight will finish/)).toBeInTheDocument()

    // Scoped to the dialog: the row's trigger carries the same label,
    // which is right for the user and ambiguous for the query.
    const dialog = screen.getByRole('alertdialog')
    await user.click(within(dialog).getByRole('button', { name: /^Remove node$/ }))
    await waitFor(() => expect(deleted).toBe(true))
  })

  test('cancelling leaves the node alone', async () => {
    let deleted = false
    server.use(
      http.delete('/v1/portal/inference/nodes/gpu-hand', () => {
        deleted = true
        return new HttpResponse(null, { status: 204 })
      }),
    )
    respondWith(
      status({
        nodes: [node({ id: 'gpu-hand', source: 'pinned' })],
            total_in_flight: 0,
            total_capacity: 64,
            utilization: 0,
      }),
    )

    renderWithProviders(<InferencePage />)
    const user = await expand('gpu-hand')
    await user.click(await screen.findByRole('button', { name: /remove node/i }))
    const dialog = await screen.findByRole('alertdialog')
    await user.click(within(dialog).getByRole('button', { name: /^Cancel$/ }))

    expect(deleted).toBe(false)
    expect(screen.getByText('gpu-hand')).toBeInTheDocument()
  })

  test('a node publishing no metrics says so rather than showing zeros', async () => {
    respondWith(status())

    renderWithProviders(<InferencePage />)
    await expand('gpu-01')

    expect(await screen.findByText('no metrics published')).toBeInTheDocument()
  })

  test('an unreachable node stays listed and explains itself', async () => {
    // The property that made pinned nodes a table rather than a TTL key.
    respondWith(
      status({
        nodes: [
              node({
                id: 'gpu-hand-01',
                source: 'pinned',
                health: { state: 'unreachable', detail: 'No response within 3s', latency_ms: null },
              }),
            ],
            total_in_flight: 0,
            total_capacity: 64,
            utilization: 0,
      }),
    )

    renderWithProviders(<InferencePage />)

    // Named twice on purpose: once in the row, once in the "Needs
    // attention" card, which is the point of that card.
    expect((await screen.findAllByText('gpu-hand-01')).length).toBeGreaterThan(0)
    await expand('gpu-hand-01')
    expect(await screen.findByText(/Unreachable — No response within 3s/)).toBeInTheDocument()
  })

  test('the status dot carries the reason for screen readers', async () => {
    // Colour is the only signal in the collapsed row, so it cannot be the
    // only signal.
    respondWith(
      status({
        nodes: [
              node({ health: { state: 'unreachable', detail: 'No response within 3s', latency_ms: null } }),
            ],
            total_in_flight: 0,
            total_capacity: 64,
            utilization: 0,
      }),
    )

    renderWithProviders(<InferencePage />)

    expect(await screen.findByLabelText('Unreachable: No response within 3s')).toBeInTheDocument()
  })

  test('how a node was added is detail, not row', async () => {
    respondWith(
      status({
        nodes: [node({ id: 'gpu-fixed' }), node({ id: 'gpu-hand', source: 'pinned' })],
            total_in_flight: 0,
            total_capacity: 128,
            utilization: 0,
      }),
    )

    renderWithProviders(<InferencePage />)
    await waitFor(() => expect(document.querySelectorAll('.inference-node-summary')).toHaveLength(2))

    // Provenance is not state: it matters when you want to change
    // something, which is when you have opened the node.
    expect(screen.queryByText(/added here/i)).not.toBeInTheDocument()

    await expand('gpu-hand')
    expect(await screen.findByText(/someone here, in the portal/i)).toBeInTheDocument()
  })

  test('a node from configuration says where to change it', async () => {
    respondWith(status())

    renderWithProviders(<InferencePage />)
    await expand('gpu-01')

    expect(await screen.findByText(/change it there and redeploy/i)).toBeInTheDocument()
  })

  test('only a node added here offers removal, and only once expanded', async () => {
    // A configured node changes by redeploy and a registered one by
    // whatever started the cluster, so a delete button for either is a
    // lie.
    respondWith(
      status({
        nodes: [node({ id: 'gpu-fixed' }), node({ id: 'gpu-hand', source: 'pinned' })],
            total_in_flight: 0,
            total_capacity: 128,
            utilization: 0,
      }),
    )

    renderWithProviders(<InferencePage />)

    expect(screen.queryByRole('button', { name: /remove node/i })).not.toBeInTheDocument()

    await expand('gpu-fixed')
    expect(screen.queryByRole('button', { name: /remove node/i })).not.toBeInTheDocument()

    await expand('gpu-hand')
    expect(await screen.findByRole('button', { name: /remove node/i })).toBeInTheDocument()
  })

  test('a heartbeat age appears only for registered nodes', async () => {
    respondWith(
      status({
        nodes: [node({ source: 'registered', last_seen_seconds_ago: 900 })],
            total_in_flight: 0,
            total_capacity: 64,
            utilization: 0,
      }),
    )

    renderWithProviders(<InferencePage />)
    await expand('gpu-01')

    expect(await screen.findByText('15m ago')).toBeInTheDocument()
  })

  test('a configured node has no heartbeat row at all', async () => {
    // Inventing an age would imply a liveness check that is not
    // happening — the dot is where its liveness comes from.
    respondWith(status())

    renderWithProviders(<InferencePage />)
    await expand('gpu-01')

    expect(await screen.findByText('no metrics published')).toBeInTheDocument()
    expect(screen.queryByText(/Last heartbeat/i)).not.toBeInTheDocument()
  })

  test('utilization is a headline number, not a footnote', async () => {
    respondWith(
      status({
        nodes: [node({ in_flight: 48 })],
            total_in_flight: 48,
            total_capacity: 64,
            utilization: 0.75,
      }),
    )

    renderWithProviders(<InferencePage />)

    // A stat card, in the idiom the rest of the portal uses for numbers.
    // The row shows a percentage too, so this is scoped to the card.
    const card = (await screen.findByText('Capacity in use')).closest('.stat-card')!
    expect(within(card as HTMLElement).getByText('75%')).toBeInTheDocument()
    expect(screen.getByText('48 of 64 concurrent')).toBeInTheDocument()
  })

  test('no nodes says so in the capacity card', async () => {
    respondWith(status({ nodes: [], total_capacity: 0 }))

    renderWithProviders(<InferencePage />)

    expect(await screen.findByText('Capacity in use')).toBeInTheDocument()
    expect(screen.getByText('no nodes')).toBeInTheDocument()
  })

  test('the row names the model a node serves', async () => {
    respondWith(status())

    renderWithProviders(<InferencePage />)

    await waitFor(() => expect(document.querySelectorAll('.inference-node-summary')).toHaveLength(1))
    expect(document.querySelector('.inference-node-models')).toHaveTextContent('igni-1.0-27b')
  })

  test('says when registration is switched off', async () => {
    respondWith(status({ registration_enabled: false }))

    renderWithProviders(<InferencePage />)

    expect(await screen.findByText(/cannot register themselves/i)).toBeInTheDocument()
  })

  test('says when it is on, and how often nodes must check in', async () => {
    respondWith(status({ registration_enabled: true, heartbeat_interval_seconds: 60 }))

    renderWithProviders(<InferencePage />)

    expect(await screen.findByText(/check in every 60s/i)).toBeInTheDocument()
  })

  test('a non-admin is told why, not shown an error', async () => {
    respondWith({ detail: 'Only org admins can view inference node statistics' }, { status: 403 })

    renderWithProviders(<InferencePage />)

    expect(await screen.findByText(/Admins only/i)).toBeInTheDocument()
  })
})

describe('Adding a node', () => {
  /**
   * The flow: paste an address, press Check, pick from what the node
   * actually serves. Typing model ids by hand is how you end up with a
   * node that is never selected — a mistyped id does not error, it simply
   * never matches a request — and LoRA adapter names are whatever the
   * operator passed to --lora-modules.
   */
  function discovers(payload: object) {
    server.use(http.post('/v1/portal/inference/nodes/discover', () => HttpResponse.json(payload)))
  }

  // Adding a node is a super admin's (SER-32).
  beforeEach(() => {
    server.use(http.get('/v1/portal/me', () => HttpResponse.json({ organizations: [{ id: 'o-1' }], is_super_admin: true })))
  })

  async function openAndCheck(url = 'http://gpu-rack-01.internal:8000/v1') {
    const user = userEvent.setup()
    await user.click(await screen.findByRole('button', { name: /add node/i }))
    await user.type(screen.getByPlaceholderText(/gpu-rack-01.internal/i), url)
    await user.click(screen.getByRole('button', { name: /^check$/i }))
    return user
  }

  test('the model list comes from the node, not from typing', async () => {
    respondWith(status())
    discovers({ reachable: true, models: ['base-27b'], detail: null, latency_ms: 9, needs_credentials: false })

    renderWithProviders(<InferencePage />)
    await openAndCheck()

    expect(await screen.findByText('base-27b')).toBeInTheDocument()
  })

  test('LoRA adapters are offered alongside the base model', async () => {
    // One GPU, three selectable ids. They share weights and the KV cache,
    // which is why they are one node rather than three.
    respondWith(status())
    discovers({
      reachable: true,
      models: ['base-27b', 'sql-expert', 'docs-writer'],
      detail: null,
      latency_ms: 9,
      needs_credentials: false,
    })

    renderWithProviders(<InferencePage />)
    await openAndCheck()

    expect(await screen.findByText('sql-expert')).toBeInTheDocument()
    expect(screen.getByText('docs-writer')).toBeInTheDocument()
    expect(screen.getByText(/3 models/i)).toBeInTheDocument()
  })

  test('a name is suggested from the address', async () => {
    respondWith(status())
    discovers({ reachable: true, models: ['base-27b'], detail: null, latency_ms: 9, needs_credentials: false })

    renderWithProviders(<InferencePage />)
    await openAndCheck()

    await screen.findByText('base-27b')
    expect(screen.getByDisplayValue('gpu-rack-01')).toBeInTheDocument()
  })

  test('a token-protected node says so instead of "0 models"', async () => {
    // The likely first result of pasting a vLLM node started with
    // --api-key: it protects /v1, and /v1/models is what we ask for.
    // Before this, the dialog reported "Reachable — 0 models", which
    // reads as a node serving nothing.
    respondWith(status())
    discovers({
      reachable: true,
      models: [],
      detail: 'The node rejected our credentials',
      latency_ms: 6,
      needs_credentials: true,
    })

    renderWithProviders(<InferencePage />)
    await openAndCheck()

    expect(await screen.findByText(/wants a token/i)).toBeInTheDocument()
    expect(screen.queryByText(/0 models/i)).not.toBeInTheDocument()
  })

  test('an unreachable address says so and still allows adding', async () => {
    // A node being brought up should be addable before it answers.
    respondWith(status())
    discovers({ reachable: false, models: [], detail: 'No response within 3s', latency_ms: null, needs_credentials: false })

    renderWithProviders(<InferencePage />)
    await openAndCheck('http://not-up-yet.internal:8000/v1')

    expect(await screen.findByText(/did not answer/i)).toBeInTheDocument()
    expect(screen.getByText(/No response within 3s/)).toBeInTheDocument()
  })

  test('nothing can be added before the address has been checked', async () => {
    // Every field below depends on what the node said.
    respondWith(status())

    renderWithProviders(<InferencePage />)
    const user = userEvent.setup()
    await user.click(await screen.findByRole('button', { name: /add node/i }))

    const submit = screen.getAllByRole('button', { name: /add node/i }).at(-1)!
    expect(submit).toBeDisabled()
  })
})

describe('Row alignment', () => {
  /**
   * Each row is its own <summary>, so its own grid container. With
   * `auto` or `minmax()` tracks every row sized its columns to its own
   * content — a node called gpu-lora-01 serving three models resolved
   * differently from gpu-offline-04 serving one — and the
   * columns visibly stepped in and out down the list.
   *
   * Fixed tracks resolve identically in every row. Asserted on the
   * declaration rather than on layout, because jsdom does not lay out.
   */
  test('every row declares the same fixed column tracks', async () => {
    respondWith(
      status({
        nodes: [
              node({ id: 'gpu-lora-01' }),
              node({ id: 'gpu-offline-04' }),
            ],
            total_in_flight: 0,
            total_capacity: 128,
            utilization: 0,
      }),
    )

    renderWithProviders(<InferencePage />)

    await waitFor(() => expect(document.querySelectorAll('.inference-node-summary')).toHaveLength(2))
    // Same class, so the same tracks — the bug was the tracks being
    // content-sized rather than the rows differing.
    const rows = [...document.querySelectorAll('.inference-node-summary')]
    expect(new Set(rows.map((r) => r.className)).size).toBe(1)
  })
})

describe('Column alignment', () => {
  /**
   * The models text used to trail the node name inside a flex, so it
   * started wherever that row's name happened to end — it landed at a
   * different x on every row and never formed a readable column.
   *
   * jsdom does not lay out, so this asserts the structure that produces
   * the alignment: name and models are siblings in the row's own grid,
   * not nested one inside the other.
   */
  test('models is a sibling column of the name, not nested inside it', async () => {
    respondWith(
      status({
        nodes: [
              node({ id: 'gpu-busy-02' }),
              node({ id: 'gpu-offline-04' }),
            ],
            total_in_flight: 0,
            total_capacity: 72,
            utilization: 0,
      }),
    )

    renderWithProviders(<InferencePage />)
    await waitFor(() => expect(document.querySelectorAll('.inference-node-summary')).toHaveLength(2))

    for (const row of document.querySelectorAll('.inference-node-summary')) {
      const name = row.querySelector('.inference-node-name')!
      const models = row.querySelector('.inference-node-models')!
      // Same parent — the grid — so both sit in their own fixed track.
      expect(models.parentElement).toBe(name.parentElement)
      expect(name.contains(models)).toBe(false)
    }
  })

  test('the queue reserves its space so it cannot shift the bar', async () => {
    // A row with a queue and a row without must still line their bars up.
    respondWith(
      status({
        nodes: [
              node({ id: 'quiet' }),
              node({
                id: 'queued',
                metrics: { running: 4, waiting: 5, kv_cache_percent: 20, prefix_cache_hit_percent: 50, preemptions: 0 },
              }),
            ],
            total_in_flight: 0,
            total_capacity: 128,
            utilization: 0,
      }),
    )

    renderWithProviders(<InferencePage />)
    await waitFor(() => expect(document.querySelectorAll('.inference-node-summary')).toHaveLength(2))

    // Present in both rows, empty in one: a cell that disappeared would
    // let the bar beside it slide.
    expect(document.querySelectorAll('.inference-node-queue')).toHaveLength(2)
  })
})

describe('Changing what a node serves', () => {
  /**
   * View only. The row used to carry two checkboxes that PATCHed on
   * change with no Save — a shape that made a half-applied edit both
   * possible and invisible, on a node with seven fields. Editing is a
   * page of its own now, reached by Edit.
   */

  async function expand(nodeId: string) {
    const user = userEvent.setup()
    const target = await waitFor(() => {
      const rows = [...document.querySelectorAll('.inference-node-summary')]
      const row = rows.find((el) => el.textContent?.includes(nodeId))
      if (!row) throw new Error(`no row for ${nodeId}`)
      return row
    })
    await user.click(target)
    return user
  }

  const pinned = { source: 'pinned' }

  test('Edit links to the node page', async () => {
    respondWith(status({ nodes: [node(pinned)], total_in_flight: 0, total_capacity: 64, utilization: 0 }))

    renderWithProviders(<InferencePage />)
    await expand('gpu-01')

    const edit = await screen.findByRole('link', { name: /Edit/i })
    expect(edit).toHaveAttribute('href', '/inference/nodes/gpu-01/edit')
  })

  test('a node defined elsewhere offers no Edit', async () => {
    // Its definition lives in the cluster or the config file, so a form
    // here could not save. Stop serving still applies to it.
    respondWith(status({ nodes: [node({ source: 'registered' })], total_in_flight: 0, total_capacity: 64, utilization: 0 }))

    renderWithProviders(<InferencePage />)
    await expand('gpu-01')

    expect(await screen.findByText(/change it in whatever starts the cluster/i)).toBeInTheDocument()
    expect(screen.queryByRole('link', { name: /Edit/i })).toBeNull()
  })
})

describe('Taking a node out of rotation', () => {
  /**
   * The switch a self-registered node never had. Its source read
   * "change it in whatever starts the cluster", because a registration is
   * idempotent and the cluster re-announces itself every 60 seconds — so
   * anything written here would be overwritten inside three minutes. The
   * drain is stored separately and outlives that, which is what makes a
   * switch here honest.
   */

  async function expand(nodeId: string) {
    const user = userEvent.setup()
    const target = await waitFor(() => {
      const rows = [...document.querySelectorAll('.inference-node-summary')]
      const row = rows.find((el) => el.textContent?.includes(nodeId))
      if (!row) throw new Error(`no row for ${nodeId}`)
      return row
    })
    await user.click(target)
    return user
  }

  const drained = {
    by: 'u-1',
    by_email: 'admin@example.com',
    at: '2026-09-02T10:00:00Z',
    reason: 'firmware update',
  }

  test('a registered node can be switched off, which it could not before', async () => {
    respondWith(status({ nodes: [node({ source: 'registered' })], total_in_flight: 0, total_capacity: 64, utilization: 0 }))

    let drainedWith: unknown = null
    server.use(
      http.post('/v1/portal/inference/nodes/gpu-01/drain', async ({ request }) => {
        drainedWith = await request.json()
        return HttpResponse.json(drained)
      }),
    )

    renderWithProviders(<InferencePage />)
    const user = await expand('gpu-01')

    await user.click(await screen.findByRole('button', { name: /Stop serving gpu-01/i }))

    await waitFor(() => expect(drainedWith).not.toBeNull())
  })

  test('a drained node says who stopped it, and why', async () => {
    respondWith(status({ nodes: [node({ drain: drained })], total_in_flight: 0, total_capacity: 0, utilization: 0 }))

    renderWithProviders(<InferencePage />)
    await expand('gpu-01')

    // Offers the opposite action, so the state is unambiguous.
    expect(await screen.findByRole('button', { name: /Resume gpu-01/i })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /Stop serving/i })).not.toBeInTheDocument()
    // The record answers "why is this off?" without an audit search.
    expect(screen.getByText(/admin@example.com/)).toBeInTheDocument()
    expect(screen.getByText(/firmware update/)).toBeInTheDocument()
  })

  test('a drained node is visible while collapsed', async () => {
    // Otherwise you have to open every row to find the GPU somebody
    // stopped last week.
    respondWith(status({ nodes: [node({ drain: drained })], total_in_flight: 0, total_capacity: 0, utilization: 0 }))

    renderWithProviders(<InferencePage />)

    expect(await screen.findByText('not serving')).toBeInTheDocument()
  })

  test('switching it back on clears the drain rather than writing a flag', async () => {
    respondWith(status({ nodes: [node({ drain: drained })], total_in_flight: 0, total_capacity: 0, utilization: 0 }))

    let resumed = false
    server.use(
      http.delete('/v1/portal/inference/nodes/gpu-01/drain', () => {
        resumed = true
        return new HttpResponse(null, { status: 204 })
      }),
    )

    renderWithProviders(<InferencePage />)
    const user = await expand('gpu-01')

    await user.click(await screen.findByRole('button', { name: /Resume gpu-01/i }))

    await waitFor(() => expect(resumed).toBe(true))
  })

  test('the node stays listed while drained', async () => {
    // A switch whose subject disappears cannot be switched back.
    respondWith(status({ nodes: [node({ drain: drained })], total_in_flight: 0, total_capacity: 0, utilization: 0 }))

    renderWithProviders(<InferencePage />)

    expect(await screen.findByText('gpu-01')).toBeInTheDocument()
  })
})

describe('An org admin who is not a super admin', () => {
  // The fleet serves every organisation, so an org admin sees it but
  // cannot change it (SER-32).
  test('sees the fleet but no controls to change it', async () => {
    respondWith(
      status({
        nodes: [node({ id: 'gpu-hand', source: 'pinned' })],
            total_in_flight: 0,
            total_capacity: 64,
            utilization: 0,
      }),
      undefined,
      { is_super_admin: false },
    )
    const user = userEvent.setup()
    renderWithProviders(<InferencePage />)

    expect(await screen.findByText(/Only a super admin can add or change nodes/)).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /Add node/ })).not.toBeInTheDocument()

    await user.click(await screen.findByText('gpu-hand'))
    expect(screen.queryByRole('button', { name: /Remove node/ })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /Stop serving/ })).not.toBeInTheDocument()
    expect(screen.queryByRole('link', { name: /Edit/ })).not.toBeInTheDocument()
  })
})
