/**
 * The dashboard: igni at a glance.
 *
 * An admin sees every area of the organisation and what needs attention
 * in it; a member sees only their own areas, and — the server refusing
 * every request it was not allowed — the dashboard never asks for an
 * admin's data on a member's behalf (the test server errors on any
 * request it has no handler for).
 */
import { describe, expect, test } from 'vitest'
import { http, HttpResponse } from 'msw'
import { screen, within } from '@testing-library/react'

import { renderWithProviders } from '../../test/render'
import { server } from '../../test/server'
import { DashboardPage } from '../DashboardPage'
import {
  ADMIN,
  API_KEYS,
  APPS,
  AUDIT,
  COMMANDS,
  ERASURES,
  GROUPS,
  INFERENCE,
  MEMBER,
  TEAM,
  USAGE,
  VM_CREDENTIALS,
  VM_PROFILES,
} from '../dashboard/__tests__/fixtures'

function everyonesReads(opts: { me?: unknown; usageEmpty?: boolean; usageError?: boolean } = {}) {
  server.use(
    http.get('/v1/portal/me', () => HttpResponse.json(opts.me ?? ADMIN)),
    http.get('/v1/portal/usage', () =>
      opts.usageError
        ? HttpResponse.json({ detail: 'boom' }, { status: 500 })
        : HttpResponse.json(opts.usageEmpty ? { ...USAGE, data: [] } : USAGE),
    ),
    http.get('/v1/portal/groups', () => HttpResponse.json(GROUPS)),
    http.get('/v1/portal/api-keys', () => HttpResponse.json({ keys: API_KEYS })),
    http.get('/v1/portal/vm-credentials', () => HttpResponse.json({ credentials: VM_CREDENTIALS })),
    http.get('/v1/vm/commands', () => HttpResponse.json({ commands: COMMANDS })),
  )
}

function adminReads() {
  server.use(
    http.get('/v1/portal/team', () => HttpResponse.json(TEAM)),
    http.get('/v1/portal/inference', () => HttpResponse.json(INFERENCE)),
    http.get('/v1/portal/apps', () => HttpResponse.json(APPS)),
    http.get('/v1/portal/orgs/org-1/vm-profiles', () => HttpResponse.json(VM_PROFILES)),
    http.get('/v1/portal/orgs/org-1/erasures', () => HttpResponse.json(ERASURES)),
    http.get('/v1/portal/audit-events', () => HttpResponse.json(AUDIT)),
  )
}

const section = (name: string) => screen.getByRole('region', { name })

describe('DashboardPage, for an admin', () => {
  test('greets them and names the organisation', async () => {
    everyonesReads()
    adminReads()
    renderWithProviders(<DashboardPage />)
    expect(await screen.findByText(/Welcome, Alex Chen\. Here is Acme at a glance\./)).toBeInTheDocument()
  })

  test('shows every area of the system', async () => {
    everyonesReads()
    adminReads()
    renderWithProviders(<DashboardPage />)
    expect(await screen.findByRole('region', { name: 'Needs attention' })).toBeInTheDocument()
    for (const name of ['Team', 'Groups', 'Inference', 'Commands in VMs', 'Access', 'VM profiles', 'Apps', 'Compliance']) {
      expect(section(name)).toBeInTheDocument()
    }
  })

  test('says what needs attention, each with the way to it', async () => {
    everyonesReads()
    adminReads()
    renderWithProviders(<DashboardPage />)
    const panel = await screen.findByRole('region', { name: 'Needs attention' })
    expect(within(panel).getByRole('link', { name: '1 inference node not healthy' })).toHaveAttribute('href', '/inference')
    expect(within(panel).getByRole('link', { name: '1 invitation not accepted yet' })).toHaveAttribute('href', '/team')
    expect(within(panel).getByRole('link', { name: "1 VM profile over the platform's caps" })).toHaveAttribute(
      'href',
      '/vm-profiles',
    )
  })

  test('the headline numbers summarise the organisation', async () => {
    everyonesReads()
    adminReads()
    renderWithProviders(<DashboardPage />)
    expect(await screen.findByText('1.5M')).toBeInTheDocument()
    expect(screen.getByText('42')).toBeInTheDocument()
    expect(await screen.findByRole('link', { name: 'People: 2' })).toHaveAttribute('href', '/team')
    expect(await screen.findByRole('link', { name: 'Inference nodes: 1 / 2' })).toHaveAttribute('href', '/inference')
  })

  test('each area previews its own things', async () => {
    everyonesReads()
    adminReads()
    renderWithProviders(<DashboardPage />)
    await screen.findByRole('region', { name: 'Needs attention' })
    expect(within(section('Team')).getByText('Alex Chen')).toBeInTheDocument()
    expect(within(section('Groups')).getByText('Everyone')).toBeInTheDocument()
    expect(within(section('Commands in VMs')).getByText('npm run build')).toBeInTheDocument()
    expect(within(section('Commands in VMs')).getByText('Failed')).toBeInTheDocument()
    expect(within(section('Apps')).getByText('Review bot')).toBeInTheDocument()
    expect(within(section('Compliance')).getByText('DELETE /v1/portal/groups/g1')).toBeInTheDocument()
  })
})

describe('DashboardPage, for a member', () => {
  test('shows only their areas, and never asks for an admin\'s data', async () => {
    everyonesReads({ me: MEMBER })
    renderWithProviders(<DashboardPage />)
    expect(await screen.findByRole('region', { name: 'Access' })).toBeInTheDocument()
    expect(section('Groups')).toBeInTheDocument()
    expect(section('Commands in VMs')).toBeInTheDocument()
    for (const name of ['Team', 'Inference', 'VM profiles', 'Apps', 'Compliance']) {
      expect(screen.queryByRole('region', { name })).not.toBeInTheDocument()
    }
    expect(screen.queryByRole('link', { name: /^People:/ })).not.toBeInTheDocument()
  })

  test('their own things still need attention', async () => {
    everyonesReads({ me: MEMBER })
    renderWithProviders(<DashboardPage />)
    const panel = await screen.findByRole('region', { name: 'Needs attention' })
    expect(within(panel).getByRole('link', { name: '1 API key expiring within 7 days' })).toBeInTheDocument()
    expect(within(panel).queryByText(/inference node/)).not.toBeInTheDocument()
  })
})

describe('DashboardPage, when data is missing', () => {
  test('says so when there is no usage yet', async () => {
    everyonesReads({ me: MEMBER, usageEmpty: true })
    renderWithProviders(<DashboardPage />)
    expect(await screen.findByText(/No usage data yet/i)).toBeInTheDocument()
  })

  test('warns when a read fails', async () => {
    everyonesReads({ me: MEMBER, usageError: true })
    renderWithProviders(<DashboardPage />)
    expect(await screen.findByText(/Some data could not be loaded/i)).toBeInTheDocument()
  })
})
