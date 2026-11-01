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
import { BROWSER, OVERVIEW } from '../../test/fixtures/vmProfiles'

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
})
