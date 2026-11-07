import { keepPreviousData, useQuery } from '@tanstack/react-query'

import client from '../../api/client'
import type {
  ApiKey,
  AuditEventPage,
  ErasureList,
  InferenceStatus,
  OrgAppList,
  OrgGroupList,
  TeamMemberList,
  UserMe,
  VmCommandRecord,
  VmCredential,
  VmProfilesOverview,
} from '../../types'
import type { UsageResponse, UsageScope } from './types'

/** How many recent commands and audit events the dashboard previews. */
export const RECENT_LIMIT = 5

/**
 * Everything the dashboard shows, each read only by someone allowed it.
 *
 * The rules are the sidebar's: what an admin-only page reads is read here
 * for an admin alone, so a member's dashboard never draws a refusal (the
 * team list and inference status are admin-only even where the page that
 * shows them is not). Query keys are the pages' own where the shape is
 * the same, so opening a page after the dashboard costs nothing.
 */
export function useDashboardData(tokenScope: UsageScope, requestScope: UsageScope) {
  const me = useQuery({
    queryKey: ['me'],
    queryFn: () => client.get<UserMe>('/portal/me').then((r) => r.data),
  })
  const isAdmin = me.data?.role === 'admin'
  const orgId = me.data?.org_id ?? null
  const signedIn = me.isSuccess
  const admin = signedIn && isAdmin

  // ``keepPreviousData`` keeps the prior chart while a scope flip
  // refetches, instead of collapsing to the empty state and back.
  const usage = (scope: UsageScope) => ({
    queryKey: ['usage', 30, scope],
    queryFn: () =>
      client
        .get<UsageResponse>('/portal/usage', { params: { days: 30, scope } })
        .then((r) => r.data),
    placeholderData: keepPreviousData,
  })
  const tokenUsage = useQuery(usage(isAdmin ? tokenScope : 'personal'))
  const requestUsage = useQuery(usage(isAdmin ? requestScope : 'personal'))

  const groups = useQuery({
    queryKey: ['groups'],
    queryFn: () => client.get<OrgGroupList>('/portal/groups').then((r) => r.data),
    enabled: signedIn,
  })
  const apiKeys = useQuery({
    queryKey: ['api-keys'],
    queryFn: () => client.get<{ keys: ApiKey[] }>('/portal/api-keys').then((r) => r.data.keys),
    enabled: signedIn,
  })
  const vmCredentials = useQuery({
    queryKey: ['vm-credentials'],
    queryFn: () =>
      client
        .get<{ credentials: VmCredential[] }>('/portal/vm-credentials')
        .then((r) => r.data.credentials),
    enabled: signedIn,
  })
  const commands = useQuery({
    queryKey: ['vm-commands', 'recent', RECENT_LIMIT * 4],
    queryFn: () =>
      client
        .get<{ commands: VmCommandRecord[] }>('/vm/commands', { params: { limit: RECENT_LIMIT * 4 } })
        .then((r) => r.data.commands),
    enabled: signedIn,
  })

  const team = useQuery({
    queryKey: ['team-members'],
    queryFn: () => client.get<TeamMemberList>('/portal/team').then((r) => r.data),
    enabled: admin,
  })
  const inference = useQuery({
    queryKey: ['inference-status'],
    queryFn: () => client.get<InferenceStatus>('/portal/inference').then((r) => r.data),
    enabled: admin,
  })
  const apps = useQuery({
    queryKey: ['apps'],
    queryFn: () => client.get<OrgAppList>('/portal/apps').then((r) => r.data),
    enabled: admin,
  })
  const vmProfiles = useQuery({
    queryKey: ['vm-profiles', orgId],
    queryFn: () =>
      client.get<VmProfilesOverview>(`/portal/orgs/${orgId}/vm-profiles`).then((r) => r.data),
    enabled: admin && orgId !== null,
  })
  const erasures = useQuery({
    queryKey: ['erasures', orgId],
    queryFn: () => client.get<ErasureList>(`/portal/orgs/${orgId}/erasures`).then((r) => r.data),
    enabled: admin && orgId !== null,
  })
  const audit = useQuery({
    queryKey: ['audit-events', 'recent', RECENT_LIMIT],
    queryFn: () =>
      client
        .get<AuditEventPage>('/portal/audit-events', { params: { limit: RECENT_LIMIT } })
        .then((r) => r.data),
    enabled: admin,
  })

  return {
    me,
    isAdmin,
    tokenUsage,
    requestUsage,
    groups,
    apiKeys,
    vmCredentials,
    commands,
    team,
    inference,
    apps,
    vmProfiles,
    erasures,
    audit,
  }
}

export type DashboardData = ReturnType<typeof useDashboardData>
