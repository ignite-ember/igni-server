/**
 * NewEntryPage tests — covers the plugin-source-URL form group
 * (only rendered when kind=plugins), the cross-field validation
 * client-side, and the POST payload shape.
 */

import { afterEach, describe, expect, test } from 'vitest'
import { http, HttpResponse } from 'msw'
import userEvent from '@testing-library/user-event'
import { screen, cleanup, waitFor } from '@testing-library/react'
import { Routes, Route } from 'react-router-dom'
import { renderWithProviders } from '../../test/render'
import { server } from '../../test/server'
import { NewEntryPage } from '../NewEntryPage'

afterEach(() => {
  cleanup()
  server.resetHandlers()
})

function renderNewEntry() {
  return renderWithProviders(
    <Routes>
      <Route path="/groups/:id/entries/new" element={<NewEntryPage />} />
    </Routes>,
    { route: '/groups/g-1/entries/new' },
  )
}

describe('NewEntryPage (plugin-source fields)', () => {
  describe('visibility', () => {
    test('source fields are hidden by default (kind=agents)', async () => {
      renderNewEntry()
      // Wait for form to mount.
      expect(await screen.findByText(/^entry name$/i)).toBeInTheDocument()
      // No source URL field visible.
      expect(screen.queryByLabelText(/source url/i)).not.toBeInTheDocument()
      expect(screen.queryByLabelText(/source ref/i)).not.toBeInTheDocument()
      expect(screen.queryByLabelText(/source subdir/i)).not.toBeInTheDocument()
    })

    test('source fields appear when kind=plugins', async () => {
      const user = userEvent.setup()
      renderNewEntry()
      // Wait for the form, then pick Plugins.
      const kindCombo = await screen.findByLabelText(/^kind$/i)
      await user.selectOptions(kindCombo, 'plugins')
      expect(await screen.findByLabelText(/source url/i)).toBeInTheDocument()
      expect(screen.getByLabelText(/source ref/i)).toBeInTheDocument()
      expect(screen.getByLabelText(/source subdir/i)).toBeInTheDocument()
    })

    test('switching away from plugins clears the source fields', async () => {
      const user = userEvent.setup()
      renderNewEntry()
      const kindCombo = await screen.findByLabelText(/^kind$/i)

      await user.selectOptions(kindCombo, 'plugins')
      const urlField = await screen.findByLabelText(/source url/i)
      await user.type(urlField, 'https://github.com/owner/repo')

      // Switch to mcps — source fields must disappear AND not be
      // re-shown when switching back (verifies the field cleanup).
      await user.selectOptions(kindCombo, 'mcps')
      await waitFor(() => {
        expect(screen.queryByLabelText(/source url/i)).not.toBeInTheDocument()
      })
    })

    test('switching back to plugins starts with empty source fields', async () => {
      const user = userEvent.setup()
      renderNewEntry()
      const kindCombo = await screen.findByLabelText(/^kind$/i)

      await user.selectOptions(kindCombo, 'plugins')
      const urlField = await screen.findByLabelText(/source url/i)
      await user.type(urlField, 'https://github.com/owner/repo')

      await user.selectOptions(kindCombo, 'mcps')
      await user.selectOptions(kindCombo, 'plugins')

      const reopenedUrl = (await screen.findAllByLabelText(/source url/i))[0]
      expect((reopenedUrl as HTMLInputElement).value).toBe('')
    })
  })

  describe('validation', () => {
    async function gotoPlugins() {
      const user = userEvent.setup()
      renderNewEntry()
      const kindCombo = await screen.findByLabelText(/^kind$/i)
      await user.selectOptions(kindCombo, 'plugins')
      return { user, kindCombo }
    }

    test('rejects malformed URL on submit', async () => {
      const { user } = await gotoPlugins()
      await user.type(
        await screen.findByLabelText(/^entry name$/i),
        'my-plugin',
      )
      await user.type(
        await screen.findByLabelText(/source url/i),
        'not a url',
      )
      await user.click(screen.getByRole('button', { name: /create entry/i }))

      await waitFor(() => {
        expect(
          screen.getByText(/source url is not a valid url/i),
        ).toBeInTheDocument()
      })
    })

    test('rejects unsupported URL scheme', async () => {
      const { user } = await gotoPlugins()
      await user.type(
        await screen.findByLabelText(/^entry name$/i),
        'my-plugin',
      )
      await user.type(
        await screen.findByLabelText(/source url/i),
        'file:///etc/passwd',
      )
      await user.click(screen.getByRole('button', { name: /create entry/i }))

      await waitFor(() => {
        expect(
          screen.getByText(
            /source url must use http, https, git, or ssh/i,
          ),
        ).toBeInTheDocument()
      })
    })

    test('content is optional when source URL is set', async () => {
      // Verify by submitting with empty content + URL and
      // observing the POST body (no client-side error thrown).
      let capturedBody: Record<string, unknown> | null = null

      server.use(
        http.post(
          '/v1/portal/groups/:groupId/entries',
          async ({ request }) => {
            capturedBody = (await request.json()) as Record<string, unknown>
            return HttpResponse.json(
              { entries: [{ ...capturedBody, id: 'created-id' }], total: 1 },
              { status: 201 },
            )
          },
        ),
      )

      const { user } = await gotoPlugins()
      await user.type(
        await screen.findByLabelText(/^entry name$/i),
        'plugin-via-url',
      )
      // Don't fill the content textarea — leave it blank.
      await user.type(
        await screen.findByLabelText(/source url/i),
        'https://github.com/owner/repo',
      )

      // The create button is inside a form, so submit it via Enter
      // rather than pointerclick (more deterministic in jsdom).
      await user.click(screen.getByRole('button', { name: /create entry/i }))

      await waitFor(() => {
        expect(capturedBody).not.toBeNull()
      })
      expect(capturedBody!.kind).toBe('plugins')
      expect(capturedBody!.source_url).toBe('https://github.com/owner/repo')
    })

    test('content is required when source URL is empty', async () => {
      const { user } = await gotoPlugins()
      await user.type(
        await screen.findByLabelText(/^entry name$/i),
        'legacy-plugin',
      )
      // The plugin template pre-fills the editor with a YAML scaffold —
      // clear it so we are testing the empty path.
      const contentArea = screen.getByTestId<HTMLTextAreaElement>('content-editor')
      await user.clear(contentArea)
      // Don't fill the source URL either.
      await user.click(screen.getByRole('button', { name: /create entry/i }))

      await waitFor(() => {
        expect(screen.getByText(/content is required/i)).toBeInTheDocument()
      })
    })
  })

  describe('submit payload', () => {
    test('non-plugin submit omits the source_* keys entirely', async () => {
      // Confirms the FE only sends source fields when kind=plugins,
      // not as `null` for non-plugin kinds — the BE Pydantic model
      // distinguishes "absent" from "null".
      let captured: Record<string, unknown> | null = null

      server.use(
        http.post(
          '/v1/portal/groups/:groupId/entries',
          async ({ request }) => {
            captured = (await request.json()) as Record<string, unknown>
            return HttpResponse.json({
              entries: [{ ...captured, id: 'created-id' }],
              total: 1,
            })
          },
        ),
      )

      const user = userEvent.setup()
      renderNewEntry()
      // Default kind is agents.
      await user.type(
        await screen.findByLabelText(/^entry name$/i),
        'agent-x',
      )
      await user.click(screen.getByRole('button', { name: /create entry/i }))

      await waitFor(() => expect(captured).not.toBeNull())
      expect('source_url' in captured!).toBe(false)
      expect('source_ref' in captured!).toBe(false)
      expect('source_subdir' in captured!).toBe(false)
    })

    test('plugin submit with full source triplet forwards all 3 fields', async () => {
      let captured: Record<string, unknown> | null = null

      server.use(
        http.post(
          '/v1/portal/groups/:groupId/entries',
          async ({ request }) => {
            captured = (await request.json()) as Record<string, unknown>
            return HttpResponse.json({
              entries: [{ ...captured, id: 'created-id' }],
              total: 1,
            })
          },
        ),
      )

      const user = userEvent.setup()
      renderNewEntry()
      const kindCombo = await screen.findByLabelText(/^kind$/i)
      await user.selectOptions(kindCombo, 'plugins')
      await user.type(
        await screen.findByLabelText(/^entry name$/i),
        'remote-plugin',
      )
      await user.type(
        await screen.findByLabelText(/source url/i),
        '  https://github.com/owner/repo  ',
      )
      await user.type(
        await screen.findByLabelText(/source ref/i),
        '  main  ',
      )
      await user.type(
        await screen.findByLabelText(/source subdir/i),
        '  plugins/x  ',
      )
      await user.click(screen.getByRole('button', { name: /create entry/i }))

      await waitFor(() => expect(captured).not.toBeNull())
      // The FE trims whitespace before sending.
      expect(captured!.source_url).toBe('https://github.com/owner/repo')
      expect(captured!.source_ref).toBe('main')
      expect(captured!.source_subdir).toBe('plugins/x')
      expect(captured!.kind).toBe('plugins')
    })

    test('plugin submit with empty source URL sends null for source_*', async () => {
      // Back-compat: plugins without a source URL still post OK.
      let captured: Record<string, unknown> | null = null

      server.use(
        http.post(
          '/v1/portal/groups/:groupId/entries',
          async ({ request }) => {
            captured = (await request.json()) as Record<string, unknown>
            return HttpResponse.json({
              entries: [{ ...captured, id: 'created-id' }],
              total: 1,
            })
          },
        ),
      )

      const user = userEvent.setup()
      renderNewEntry()
      const kindCombo = await screen.findByLabelText(/^kind$/i)
      await user.selectOptions(kindCombo, 'plugins')
      await user.type(
        await screen.findByLabelText(/^entry name$/i),
        'legacy-plugin',
      )
      // Type legacy YAML content
      const contentArea = await screen.findByTestId('content-editor')
      await user.type(contentArea, 'name: legacy')
      // No source URL — leave blank.
      await user.click(screen.getByRole('button', { name: /create entry/i }))

      await waitFor(() => expect(captured).not.toBeNull())
      expect(captured!.kind).toBe('plugins')
      expect(captured!.source_url).toBeNull()
      expect(captured!.source_ref).toBeNull()
      expect(captured!.source_subdir).toBeNull()
    })

    test('BE 422 error surfaces as the content error banner', async () => {
      server.use(
        http.post('/v1/portal/groups/:groupId/entries', () =>
          HttpResponse.json(
            { detail: "Kind 'plugins' requires content_type 'yaml'" },
            { status: 422 },
          ),
        ),
      )

      const user = userEvent.setup()
      renderNewEntry()
      const kindCombo = await screen.findByLabelText(/^kind$/i)
      await user.selectOptions(kindCombo, 'plugins')
      await user.type(
        await screen.findByLabelText(/^entry name$/i),
        'agentic-eval',
      )
      // Type markdown content that the BE will reject.
      await user.click(screen.getByRole('button', { name: /create entry/i }))

      await waitFor(() => {
        expect(
          screen.getByText(/kind 'plugins' requires content_type 'yaml'/i),
        ).toBeInTheDocument()
      })
    })
  })

  describe('navigation on success', () => {
    test('success navigates to /groups/:id?tab=entries', async () => {
      // The component navigates to ``/groups/:id?tab=entries``
      // after a successful POST. Catch the navigation by mounting a
      // sentinel route at that path — when the form navigates,
      // the sentinel element becomes visible.
      server.use(
        http.post('/v1/portal/groups/:groupId/entries', () =>
          HttpResponse.json({
            entries: [{ id: 'created-id', entry_name: 'x' }],
            total: 1,
          }),
        ),
      )

      const user = userEvent.setup()
      renderWithProviders(
        <Routes>
          <Route
            path="/groups/:id/entries/new"
            element={<NewEntryPage />}
          />
          <Route
            path="/groups/:id"
            element={<div data-testid="landed-on-detail">landed on detail</div>}
          />
        </Routes>,
        { route: '/groups/g-1/entries/new' },
      )

      const kindCombo = await screen.findByLabelText(/^kind$/i)
      await user.selectOptions(kindCombo, 'plugins')
      await user.type(
        await screen.findByLabelText(/^entry name$/i),
        'ok-plugin',
      )
      await user.click(screen.getByRole('button', { name: /create entry/i }))

      // The success callback calls navigate('/groups/g-1?tab=entries')
      // — MemoryRouter routes that to the sentinel Route above.
      expect(
        await screen.findByTestId('landed-on-detail'),
      ).toBeInTheDocument()
    })
  })
})

describe('the model an agent runs against', () => {
  /**
   * A group pins one model for everything its people send, which cannot
   * express "contract review on this adapter, case summaries on that
   * one". So an agent may name its own, and inherits when it does not.
   *
   * A list rather than a text field: adapter names are whatever somebody
   * passed to `--lora-modules`, and typing one from memory is how an
   * agent ends up pointed at a model nothing serves.
   */
  function withModels(models: string[] = ['igni-1.0-27b', 'legal-reviewer']) {
    server.use(
      http.get('/v1/portal/inference/models', () => HttpResponse.json({ models })),
      http.get('/v1/portal/groups/g-1', () =>
        HttpResponse.json({
          id: 'g-1',
          org_id: 'demo-org-001',
          name: 'Legal',
          description: null,
          default_model: 'igni-1.0-27b',
          created_by_user_id: 'u1',
          created_at: '2026-01-01T00:00:00Z',
          updated_at: '2026-01-01T00:00:00Z',
          member_count: 0,
          entry_count: 0,
        }),
      ),
    )
  }

  function capturePost() {
    const captured: { body: Record<string, unknown> | null } = { body: null }
    server.use(
      http.post('/v1/portal/groups/:groupId/entries', async ({ request }) => {
        captured.body = (await request.json()) as Record<string, unknown>
        return HttpResponse.json({ entries: [], total: 0 }, { status: 201 })
      }),
    )
    return captured
  }

  test('the field is offered for agents', async () => {
    withModels()
    renderNewEntry()

    expect(await screen.findByText(/^model$/i)).toBeInTheDocument()
  })

  test('it is not offered for the other kinds, which have no model to run against', async () => {
    withModels()
    const user = userEvent.setup()
    renderNewEntry()

    await user.selectOptions(await screen.findByLabelText(/^kind$/i), 'mcps')
    expect(screen.queryByText(/^model$/i)).not.toBeInTheDocument()
  })

  test('it shows what the agent inherits when it names none', async () => {
    withModels()
    renderNewEntry()

    expect(await screen.findByRole('button', { name: /Group default \(igni-1\.0-27b\)/i })).toBeInTheDocument()
  })

  test('an agent that names none sends no model at all', async () => {
    withModels()
    const captured = capturePost()
    const user = userEvent.setup()
    renderNewEntry()

    await user.type(await screen.findByLabelText(/^entry name$/i), 'reviewer')
    await user.click(screen.getByRole('button', { name: /create entry/i }))

    await waitFor(() => expect(captured.body).not.toBeNull())
    expect(captured.body!.model).toBeUndefined()
  })

  test('picking one sends it', async () => {
    withModels()
    const captured = capturePost()
    const user = userEvent.setup()
    renderNewEntry()

    await user.type(await screen.findByLabelText(/^entry name$/i), 'contracts')
    await user.click(await screen.findByRole('button', { name: /Group default/i }))
    await user.click(await screen.findByRole('menuitemradio', { name: /^legal-reviewer$/i }))
    await user.click(screen.getByRole('button', { name: /create entry/i }))

    await waitFor(() => expect(captured.body).not.toBeNull())
    expect(captured.body!.model).toBe('legal-reviewer')
  })

  test('leaving agents drops the model, which would otherwise ride along', async () => {
    withModels()
    const captured = capturePost()
    const user = userEvent.setup()
    renderNewEntry()

    await user.type(await screen.findByLabelText(/^entry name$/i), 'server')
    await user.click(await screen.findByRole('button', { name: /Group default/i }))
    await user.click(await screen.findByRole('menuitemradio', { name: /^legal-reviewer$/i }))
    await user.selectOptions(await screen.findByLabelText(/^kind$/i), 'mcps')
    await user.click(screen.getByRole('button', { name: /create entry/i }))

    await waitFor(() => expect(captured.body).not.toBeNull())
    expect(captured.body!.model).toBeUndefined()
  })
})

describe('the breadcrumb', () => {
  /**
   * It read the group name from `useParams`, but the route is
   * `/groups/:id/entries/new` — there is no such param, so the crumb
   * rendered empty and the trail read "Groups / / New entry". The name
   * comes from the group the page already fetches.
   */
  test('names the group between Groups and New entry', async () => {
    server.use(
      http.get('/v1/portal/groups/g-1', () =>
        HttpResponse.json({
          id: 'g-1',
          org_id: 'demo-org-001',
          name: 'Legal',
          description: null,
          default_model: null,
          is_default: false,
          created_by_user_id: 'u1',
          created_at: '2026-01-01T00:00:00Z',
          updated_at: '2026-01-01T00:00:00Z',
          member_count: 0,
          entry_count: 0,
        }),
      ),
    )
    renderNewEntry()

    const crumb = await screen.findByRole('link', { name: 'Legal' })
    expect(crumb).toHaveAttribute('href', '/groups/g-1')
  })
})

describe('a tool that does not exist', () => {
  /**
   * The server accepts the entry and says so — a copy of igni's tool
   * list can lag, so an unknown name is a warning rather than a
   * refusal. What matters here is that somebody sees it: navigating
   * away on success would flash it past them on their way out.
   */
  function saveReturning(warnings: string[]) {
    server.use(
      http.get('/v1/portal/inference/models', () => HttpResponse.json({ models: [] })),
      http.post('/v1/portal/groups/:groupId/entries', () =>
        HttpResponse.json({ entries: [], total: 0, warnings }, { status: 201 }),
      ),
    )
  }

  test('the warning is shown', async () => {
    saveReturning(["'WebSarch' (did you mean 'WebSearch'?) is not a tool igni knows."])
    const user = userEvent.setup()
    renderNewEntry()

    await user.type(await screen.findByLabelText(/^entry name$/i), 'reviewer')
    await user.click(screen.getByRole('button', { name: /create entry/i }))

    expect(await screen.findByText(/did you mean 'WebSearch'/)).toBeInTheDocument()
  })

  test('it says the entry was saved anyway', async () => {
    saveReturning(["'WebSarch' is not a tool igni knows."])
    const user = userEvent.setup()
    renderNewEntry()

    await user.type(await screen.findByLabelText(/^entry name$/i), 'reviewer')
    await user.click(screen.getByRole('button', { name: /create entry/i }))

    expect(await screen.findByText(/saved, but this will not run/i)).toBeInTheDocument()
  })

  test('and keeps you on the page to fix it', async () => {
    saveReturning(["'WebSarch' is not a tool igni knows."])
    const user = userEvent.setup()
    renderNewEntry()

    await user.type(await screen.findByLabelText(/^entry name$/i), 'reviewer')
    await user.click(screen.getByRole('button', { name: /create entry/i }))

    await screen.findByText(/saved, but this will not run/i)
    expect(screen.getByLabelText(/^entry name$/i)).toBeInTheDocument()
  })

  test('a clean save says nothing and moves on', async () => {
    saveReturning([])
    const user = userEvent.setup()
    renderNewEntry()

    await user.type(await screen.findByLabelText(/^entry name$/i), 'reviewer')
    await user.click(screen.getByRole('button', { name: /create entry/i }))

    await waitFor(() =>
      expect(screen.queryByText(/saved, but this will not run/i)).not.toBeInTheDocument(),
    )
  })
})

describe('the live tool check', () => {
  /**
   * The same question the server answers on save, asked while somebody
   * is still typing — so the typo is caught before it is written.
   */
  function renderWithTools(tools: string[]) {
    server.use(
      http.get('/v1/portal/agent-tools', () => HttpResponse.json({ tools })),
      http.get('/v1/portal/inference/models', () => HttpResponse.json({ models: [] })),
    )
    renderNewEntry()
  }

  test('a mistyped tool is flagged as it is typed', async () => {
    renderWithTools(['Bash', 'WebSearch'])
    const user = userEvent.setup()

    const editor = await screen.findByTestId('content-editor')
    await user.clear(editor)
    await user.paste('---\nname: r\ntools: WebSarch\n---\nBody.')

    expect(await screen.findByText(/did you mean WebSearch/)).toBeInTheDocument()
  })

  test('real tools say nothing', async () => {
    renderWithTools(['Bash', 'WebSearch'])
    const user = userEvent.setup()

    const editor = await screen.findByTestId('content-editor')
    await user.clear(editor)
    await user.paste('---\nname: r\ntools: WebSearch, Bash\n---\nBody.')

    await waitFor(() =>
      expect(screen.queryByText(/is not a tool igni knows/)).not.toBeInTheDocument(),
    )
  })

  test('an MCP tool is not a typo', async () => {
    renderWithTools(['Bash', 'WebSearch'])
    const user = userEvent.setup()

    const editor = await screen.findByTestId('content-editor')
    await user.clear(editor)
    await user.paste('---\nname: r\ntools: MCP:github\n---\nBody.')

    await waitFor(() =>
      expect(screen.queryByText(/is not a tool igni knows/)).not.toBeInTheDocument(),
    )
  })

  test('it says what will happen', async () => {
    renderWithTools(['Bash', 'WebSearch'])
    const user = userEvent.setup()

    const editor = await screen.findByTestId('content-editor')
    await user.clear(editor)
    await user.paste('---\nname: r\ntools: Frobnicate\n---\nBody.')

    expect(await screen.findByText(/will fail when something calls it/)).toBeInTheDocument()
  })
})
