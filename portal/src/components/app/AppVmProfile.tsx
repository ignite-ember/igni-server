import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { FormControl, Select } from '@primer/react'

import client from '../../api/client'
import { refusalOf } from '../../api/refusal'
import type { OrgApp, VmProfilesOverview } from '../../types'
import type { OnFeedback } from './feedback'

/** Which VM profile the app's microVM gets: one of the organisation's, or its default. */
export function AppVmProfile({ app, orgId, onFeedback }: { app: OrgApp; orgId: string; onFeedback: OnFeedback }) {
  const queryClient = useQueryClient()
  const profiles = useQuery({
    queryKey: ['vm-profiles', orgId],
    queryFn: () => client.get<VmProfilesOverview>(`/portal/orgs/${orgId}/vm-profiles`).then((r) => r.data),
    enabled: !!orgId,
  })
  const assign = useMutation({
    mutationFn: (profileId: string | null) =>
      client.put(`/portal/orgs/${orgId}/apps/${app.app_id}/vm-profile`, { profile_id: profileId }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['app', app.app_id] })
      queryClient.invalidateQueries({ queryKey: ['vm-profiles', orgId] })
      onFeedback('success', 'VM profile changed')
    },
    onError: (error) => onFeedback('critical', refusalOf(error)),
  })

  return (
    <FormControl>
      <FormControl.Label>VM profile</FormControl.Label>
      <FormControl.Caption>What this app&apos;s microVM gets in the cloud.</FormControl.Caption>
      <Select
        value={app.vm_profile_id ?? ''}
        onChange={(e) => assign.mutate(e.target.value || null)}
        disabled={!profiles.data || assign.isPending}
      >
        <Select.Option value="">The organisation&apos;s default</Select.Option>
        {(profiles.data?.profiles ?? [])
          .filter((p) => !p.is_default)
          .map((p) => (
            <Select.Option key={p.id} value={p.id}>
              {p.name}
            </Select.Option>
          ))}
      </Select>
    </FormControl>
  )
}
