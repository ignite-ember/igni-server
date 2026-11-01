/**
 * Cloud limits: the super admin's caps on every VM profile, and each
 * organisation's quota.
 */
import { describe, expect, test } from 'vitest'
import { http, HttpResponse } from 'msw'
import userEvent from '@testing-library/user-event'
import { screen } from '@testing-library/react'
import { renderWithProviders } from '../../test/render'
import { server } from '../../test/server'
import { CloudLimitsPage } from '../CloudLimitsPage'

const BOSS = {
  user_id: 'boss',
  email: 'boss@acmecorp.io',
  full_name: 'The Boss',
  created_at: '2026-01-01T00:00:00Z',
  organizations: [],
  is_super_admin: true,
}

describe('CloudLimitsPage', () => {
  test('saves the caps, with no limit on idle time', async () => {
    let sent: unknown = null
    server.use(
      http.get('/v1/portal/me', () => HttpResponse.json(BOSS)),
      http.get('/v1/portal/vm-caps', () =>
        HttpResponse.json({ max_memory_mib: 8192, max_vcpus: 4, max_idle_before_pause_s: 3600, updated_at: null }),
      ),
      http.get('/v1/portal/deployment/organizations', () =>
        HttpResponse.json([{ id: 'o1', slug: 'acme', display_name: 'Acme', domains: [], members: 3 }]),
      ),
      http.get('/v1/portal/orgs/o1/vm-quota', () =>
        HttpResponse.json({ org_id: 'o1', awake_vm_seconds_per_month: null, enforced: false, updated_at: null }),
      ),
      http.put('/v1/portal/vm-caps', async ({ request }) => {
        sent = await request.json()
        return HttpResponse.json({ max_memory_mib: 8192, max_vcpus: 4, max_idle_before_pause_s: null })
      }),
    )

    renderWithProviders(<CloudLimitsPage />)
    await userEvent.click(await screen.findByRole('checkbox', { name: /No limit on idle time/ }))
    await userEvent.click(screen.getByRole('button', { name: 'Save limits' }))

    await expect.poll(() => sent).toEqual({
      max_memory_mib: 8192,
      max_vcpus: 4,
      max_idle_before_pause_s: null,
      max_erase_after_idle_days: null,
    })
    expect(await screen.findByText('Acme: awake VM-seconds a month')).toBeInTheDocument()
  })

  test("sets the longest an app's VM may go unwoken, keeping the volume cap as it was", async () => {
    let sent: unknown = null
    server.use(
      http.get('/v1/portal/me', () => HttpResponse.json(BOSS)),
      http.get('/v1/portal/vm-caps', () =>
        HttpResponse.json({
          max_memory_mib: 8192,
          max_vcpus: 4,
          max_idle_before_pause_s: 3600,
          max_volume_gib: 300,
          max_erase_after_idle_days: null,
          updated_at: null,
        }),
      ),
      http.get('/v1/portal/deployment/organizations', () => HttpResponse.json([])),
      http.put('/v1/portal/vm-caps', async ({ request }) => {
        sent = await request.json()
        return HttpResponse.json({ max_memory_mib: 8192, max_vcpus: 4, max_idle_before_pause_s: 3600 })
      }),
    )

    renderWithProviders(<CloudLimitsPage />)
    const field = await screen.findByRole('spinbutton', { name: /unwoken before it is erased/ })
    expect(screen.getByText(/People's VMs are never erased this way/)).toBeInTheDocument()
    await userEvent.type(field, '90')
    await userEvent.click(screen.getByRole('button', { name: 'Save limits' }))

    await expect.poll(() => sent).toEqual({
      max_memory_mib: 8192,
      max_vcpus: 4,
      max_idle_before_pause_s: 3600,
      max_volume_gib: 300,
      max_erase_after_idle_days: 90,
    })
  })

  test('is not for anybody else', async () => {
    server.use(http.get('/v1/portal/me', () => HttpResponse.json({ ...BOSS, is_super_admin: false })))

    renderWithProviders(<CloudLimitsPage />)

    expect(await screen.findByText('Super admins only')).toBeInTheDocument()
  })
})
