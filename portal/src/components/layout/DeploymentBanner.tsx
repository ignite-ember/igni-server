import { Banner, Stack } from '@primer/react'
import { useQuery } from '@tanstack/react-query'
import client from '../../api/client'

/**
 * What the deployment's startup checks warn about, for the super admins
 * who can act on it (`GET /portal/deployment/notices`): sign-in codes
 * being written to the server log (F35), a key rotation left
 * half-finished, a setting that cannot work. The server's own sentences,
 * one banner each; nothing when there is nothing to say, and never asked
 * for by anybody else, who would be refused.
 */
export function DeploymentBanner({ isSuperAdmin }: { isSuperAdmin: boolean }) {
  const notices = useQuery({
    queryKey: ['deployment-notices'],
    queryFn: () => client.get<{ notices: string[] }>('/portal/deployment/notices').then((r) => r.data),
    enabled: isSuperAdmin,
    staleTime: 60 * 60 * 1000,
  })
  const messages = isSuperAdmin ? (notices.data?.notices ?? []) : []
  if (messages.length === 0) return null
  return (
    <Stack gap="condensed" className="deployment-banner">
      {messages.map((message) => (
        <Banner key={message} variant="warning" title="Deployment" hideTitle description={message} />
      ))}
    </Stack>
  )
}
