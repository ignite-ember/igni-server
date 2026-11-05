/**
 * VM profiles: the organisation's profiles, defining one, and giving one
 * to a group.
 */
import { describe, expect, test } from 'vitest'
import { http, HttpResponse } from 'msw'
import userEvent from '@testing-library/user-event'
import { screen } from '@testing-library/react'
import { renderWithProviders } from '../../test/render'
import { server } from '../../test/server'
import { VmProfilesPage } from '../VmProfilesPage'
import { BROWSER, BROWSER_REQUEST, EXTENSION_REQUEST, INTERNET_REQUEST, OVERVIEW } from '../../test/fixtures/vmProfiles'

describe('VmProfilesPage', () => {
  test('lists the profiles, the default marked and one over the limits flagged', async () => {
    server.use(http.get('/v1/portal/orgs/demo-org-001/vm-profiles', () => HttpResponse.json(OVERVIEW)))

    renderWithProviders(<VmProfilesPage />)

    expect((await screen.findAllByText('Browser')).length).toBeGreaterThan(0)
    expect(screen.getByText('Default', { selector: 'span' })).toBeInTheDocument()
    expect(screen.getByText('Over the platform limits')).toBeInTheDocument()
    expect(screen.getByText('never')).toBeInTheDocument()
  })

  test('defines a profile with its packages', async () => {
    let sent: Record<string, unknown> | null = null
    server.use(
      http.get('/v1/portal/orgs/demo-org-001/vm-profiles', () => HttpResponse.json(OVERVIEW)),
      http.post('/v1/portal/orgs/demo-org-001/vm-profiles', async ({ request }) => {
        sent = (await request.json()) as Record<string, unknown>
        return HttpResponse.json(BROWSER, { status: 201 })
      }),
    )

    renderWithProviders(<VmProfilesPage />)
    await userEvent.click(await screen.findByRole('button', { name: 'New profile' }))
    await userEvent.type(screen.getByRole('textbox', { name: /^Name/ }), 'Headless')
    await userEvent.type(screen.getByRole('textbox', { name: /apt packages/ }), 'chromium ffmpeg')
    await userEvent.click(screen.getByRole('button', { name: 'Save profile' }))

    await expect.poll(() => sent?.name).toBe('Headless')
    expect(sent!.packages).toEqual({ apt: ['chromium', 'ffmpeg'], pip: [], npm: [] })
    expect(sent!.idle_before_pause_s).toBe(10)
    expect([sent!.memory_mib, sent!.normal_memory_mib, sent!.expected_memory_mib]).toEqual([4096, 1024, null])
    expect(sent).not.toHaveProperty('drop_caches_before_suspend')
  })

  test('a new profile is internal-only unless internet access is ticked', async () => {
    let sent: Record<string, unknown> | null = null
    server.use(
      http.get('/v1/portal/orgs/demo-org-001/vm-profiles', () => HttpResponse.json(OVERVIEW)),
      http.post('/v1/portal/orgs/demo-org-001/vm-profiles', async ({ request }) => {
        sent = (await request.json()) as Record<string, unknown>
        return HttpResponse.json(BROWSER, { status: 201 })
      }),
    )

    renderWithProviders(<VmProfilesPage />)
    await userEvent.click(await screen.findByRole('button', { name: 'New profile' }))
    await userEvent.type(screen.getByRole('textbox', { name: /^Name/ }), 'Researcher')
    expect(screen.getByRole('checkbox', { name: 'Internet access' })).not.toBeChecked()
    expect(screen.getByText(/Security-relevant/)).toBeInTheDocument()
    expect(screen.queryByText('This profile grants internet access')).not.toBeInTheDocument()
    await userEvent.click(screen.getByRole('checkbox', { name: 'Internet access' }))
    expect(screen.getByText('This profile grants internet access')).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: 'Save profile' }))

    await expect.poll(() => sent?.name).toBe('Researcher')
    expect(sent!.internet).toBe(true)
  })

  test('shows which profiles grant internet access', async () => {
    server.use(
      http.get('/v1/portal/orgs/demo-org-001/vm-profiles', () =>
        HttpResponse.json({ ...OVERVIEW, profiles: [OVERVIEW.profiles[0], { ...BROWSER, internet: true }] }),
      ),
    )

    renderWithProviders(<VmProfilesPage />)

    expect(await screen.findByLabelText('Browser grants internet access')).toHaveTextContent('Internet')
    expect(screen.getByText('Internal only')).toBeInTheDocument()
  })

  test('sends the expected memory when one is given', async () => {
    let sent: Record<string, unknown> | null = null
    server.use(
      http.get('/v1/portal/orgs/demo-org-001/vm-profiles', () => HttpResponse.json(OVERVIEW)),
      http.post('/v1/portal/orgs/demo-org-001/vm-profiles', async ({ request }) => {
        sent = (await request.json()) as Record<string, unknown>
        return HttpResponse.json(BROWSER, { status: 201 })
      }),
    )

    renderWithProviders(<VmProfilesPage />)
    await userEvent.click(await screen.findByRole('button', { name: 'New profile' }))
    await userEvent.type(screen.getByRole('textbox', { name: /^Name/ }), 'Builder')
    await userEvent.type(screen.getByRole('spinbutton', { name: /Expected memory/ }), '1536')
    await userEvent.click(screen.getByRole('button', { name: 'Save profile' }))

    await expect.poll(() => sent?.name).toBe('Builder')
    expect(sent!.expected_memory_mib).toBe(1536)
  })

  test("shows how each profile treats an app's unused VM", async () => {
    server.use(http.get('/v1/portal/orgs/demo-org-001/vm-profiles', () => HttpResponse.json(OVERVIEW)))

    renderWithProviders(<VmProfilesPage />)

    expect(await screen.findByText('Erased after 30 days unused')).toBeInTheDocument()
    expect(screen.getByText('Kept')).toBeInTheDocument()
  })

  test('erases an app\'s VM after days unused, explaining it is for apps only', async () => {
    let sent: Record<string, unknown> | null = null
    server.use(
      http.get('/v1/portal/orgs/demo-org-001/vm-profiles', () => HttpResponse.json(OVERVIEW)),
      http.post('/v1/portal/orgs/demo-org-001/vm-profiles', async ({ request }) => {
        sent = (await request.json()) as Record<string, unknown>
        return HttpResponse.json(BROWSER, { status: 201 })
      }),
    )

    renderWithProviders(<VmProfilesPage />)
    await userEvent.click(await screen.findByRole('button', { name: 'New profile' }))
    expect(screen.getByText(/Apps and their spaces only/)).toBeInTheDocument()
    expect(screen.getByText(/never erased this way while they are a member/)).toBeInTheDocument()
    await userEvent.type(screen.getByRole('textbox', { name: /^Name/ }), 'Quarterly')
    await userEvent.type(screen.getByRole('spinbutton', { name: /days unused/ }), '30')
    await userEvent.click(screen.getByRole('button', { name: 'Save profile' }))

    await expect.poll(() => sent?.name).toBe('Quarterly')
    expect(sent!.erase_after_idle_days).toBe(30)
  })

  test('leaves it empty for never, and says what the platform requires', async () => {
    let sent: Record<string, unknown> | null = null
    server.use(
      http.get('/v1/portal/orgs/demo-org-001/vm-profiles', () =>
        HttpResponse.json({ ...OVERVIEW, caps: { ...OVERVIEW.caps, max_erase_after_idle_days: 90 } }),
      ),
      http.post('/v1/portal/orgs/demo-org-001/vm-profiles', async ({ request }) => {
        sent = (await request.json()) as Record<string, unknown>
        return HttpResponse.json(
          { detail: 'A VM not woken for 90 days must have its workspace erased on this platform' },
          { status: 400 },
        )
      }),
    )

    renderWithProviders(<VmProfilesPage />)
    await userEvent.click(await screen.findByRole('button', { name: 'New profile' }))
    expect(screen.getByText(/This platform requires at most 90 days/)).toBeInTheDocument()
    await userEvent.type(screen.getByRole('textbox', { name: /^Name/ }), 'Forever')
    await userEvent.click(screen.getByRole('button', { name: 'Save profile' }))

    await expect.poll(() => sent?.name).toBe('Forever')
    expect(sent!.erase_after_idle_days).toBeNull()
    expect(await screen.findByText(/must have its workspace erased/)).toBeInTheDocument()
  })

  test('gives a group a profile', async () => {
    let assigned: unknown = null
    server.use(
      http.get('/v1/portal/orgs/demo-org-001/vm-profiles', () => HttpResponse.json(OVERVIEW)),
      http.put('/v1/portal/orgs/demo-org-001/groups/g-legal/vm-profile', async ({ request }) => {
        assigned = await request.json()
        return new HttpResponse(null, { status: 204 })
      }),
    )

    renderWithProviders(<VmProfilesPage />)
    await userEvent.selectOptions(await screen.findByRole('combobox', { name: 'VM profile of Legal' }), 'p-browser')

    await expect.poll(() => assigned).toEqual({ profile_id: 'p-browser' })
  })

  test("shows the server's refusal for somebody who is not an admin", async () => {
    server.use(
      http.get('/v1/portal/orgs/demo-org-001/vm-profiles', () =>
        HttpResponse.json({ detail: 'Only an admin of this organisation, or a super admin' }, { status: 403 }),
      ),
    )

    renderWithProviders(<VmProfilesPage />)

    expect(await screen.findByText('Only an admin of this organisation, or a super admin')).toBeInTheDocument()
  })

  describe('requests from apps and extensions', () => {
    const REQUESTS = '/v1/portal/orgs/demo-org-001/vm-profile-requests'

    test('shows exactly what would run as root, and says nothing runs before approval', async () => {
      server.use(
        http.get('/v1/portal/orgs/demo-org-001/vm-profiles', () => HttpResponse.json(OVERVIEW)),
        http.get(REQUESTS, () => HttpResponse.json({ requests: [BROWSER_REQUEST] })),
      )

      renderWithProviders(<VmProfilesPage />)

      expect(await screen.findByText('App Review bot')).toBeInTheDocument()
      expect(screen.getByText('Pending')).toBeInTheDocument()
      expect(screen.getByText(/@playwright\/mcp@0.0.84/)).toBeInTheDocument()
      expect(screen.getByLabelText('Setup script of App Review bot')).toHaveTextContent(
        'ln -sf /opt/chromium/chrome-linux/chrome /opt/google/chrome/chrome',
      )
      expect(screen.getByText(/Nothing they ask for runs until you approve it/)).toBeInTheDocument()
    })

    test('approves the version it showed', async () => {
      let sent: unknown = null
      server.use(
        http.get('/v1/portal/orgs/demo-org-001/vm-profiles', () => HttpResponse.json(OVERVIEW)),
        http.get(REQUESTS, () => HttpResponse.json({ requests: [BROWSER_REQUEST] })),
        http.post(`${REQUESTS}/r-bot/approve`, async ({ request }) => {
          sent = await request.json()
          return HttpResponse.json({ ...BROWSER_REQUEST, state: 'approved', profile_id: 'p-bot' })
        }),
      )

      renderWithProviders(<VmProfilesPage />)
      await userEvent.click(await screen.findByRole('button', { name: 'Approve App Review bot' }))

      await expect.poll(() => sent).toEqual({ version: 'v-browser-1' })
      expect(await screen.findByText('Approved: Review bot has its profile')).toBeInTheDocument()
    })

    test('rejects with a reason', async () => {
      let sent: unknown = null
      server.use(
        http.get('/v1/portal/orgs/demo-org-001/vm-profiles', () => HttpResponse.json(OVERVIEW)),
        http.get(REQUESTS, () => HttpResponse.json({ requests: [BROWSER_REQUEST] })),
        http.post(`${REQUESTS}/r-bot/reject`, async ({ request }) => {
          sent = await request.json()
          return HttpResponse.json({ ...BROWSER_REQUEST, state: 'rejected' })
        }),
      )

      renderWithProviders(<VmProfilesPage />)
      await userEvent.type(
        await screen.findByRole('textbox', { name: 'Reason for rejecting App Review bot' }),
        'Use the shared browser profile',
      )
      await userEvent.click(screen.getByRole('button', { name: 'Reject App Review bot' }))

      await expect.poll(() => sent).toEqual({ reason: 'Use the shared browser profile' })
      expect(await screen.findByText(/the VM keeps the profile it has/)).toBeInTheDocument()
    })

    test("shows the server's refusal when the request changed since it was read", async () => {
      server.use(
        http.get('/v1/portal/orgs/demo-org-001/vm-profiles', () => HttpResponse.json(OVERVIEW)),
        http.get(REQUESTS, () => HttpResponse.json({ requests: [BROWSER_REQUEST] })),
        http.post(`${REQUESTS}/r-bot/approve`, () =>
          HttpResponse.json(
            { detail: 'The request has changed since it was read: read it again before approving' },
            { status: 409 },
          ),
        ),
      )

      renderWithProviders(<VmProfilesPage />)
      await userEvent.click(await screen.findByRole('button', { name: 'Approve App Review bot' }))

      expect(await screen.findByText(/has changed since it was read/)).toBeInTheDocument()
    })

    test("an app's request for internet access is shown as such, and approving it says so", async () => {
      let sent: unknown = null
      server.use(
        http.get('/v1/portal/orgs/demo-org-001/vm-profiles', () => HttpResponse.json(OVERVIEW)),
        http.get(REQUESTS, () => HttpResponse.json({ requests: [INTERNET_REQUEST, BROWSER_REQUEST] })),
        http.post(`${REQUESTS}/r-web/approve`, async ({ request }) => {
          sent = await request.json()
          return HttpResponse.json({ ...INTERNET_REQUEST, state: 'approved', profile_id: 'p-web' })
        }),
      )

      renderWithProviders(<VmProfilesPage />)

      const asking = await screen.findByRole('region', { name: 'Request of App Web researcher' })
      expect(asking).toHaveTextContent('Internet access')
      expect(asking).toHaveTextContent(/approving lets its VM reach the internet/)
      const internal = screen.getByRole('region', { name: 'Request of App Review bot' })
      expect(internal).toHaveTextContent('Internal network only.')
      expect(screen.getByRole('button', { name: 'Approve App Review bot' })).toHaveTextContent(/^Approve$/)

      await userEvent.click(screen.getByRole('button', { name: 'Approve App Web researcher, with internet access' }))

      await expect.poll(() => sent).toEqual({ version: 'v-web-1' })
    })

    test("an extension's request over the limits cannot be approved", async () => {
      server.use(
        http.get('/v1/portal/orgs/demo-org-001/vm-profiles', () => HttpResponse.json(OVERVIEW)),
        http.get(REQUESTS, () => HttpResponse.json({ requests: [EXTENSION_REQUEST] })),
      )

      renderWithProviders(<VmProfilesPage />)

      expect(await screen.findByText('Extension Browser tools')).toBeInTheDocument()
      expect(screen.getByRole('button', { name: 'Approve Extension Browser tools' })).toBeDisabled()
      expect(screen.getByText('No setup script.')).toBeInTheDocument()
    })

    test('says when nobody has asked', async () => {
      server.use(http.get('/v1/portal/orgs/demo-org-001/vm-profiles', () => HttpResponse.json(OVERVIEW)))

      renderWithProviders(<VmProfilesPage />)

      expect(await screen.findByText('No app or extension has asked for anything.')).toBeInTheDocument()
    })
  })
})
