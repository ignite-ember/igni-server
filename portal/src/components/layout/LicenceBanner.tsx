import { Banner, Stack } from '@primer/react'
import { useQuery } from '@tanstack/react-query'
import client from '../../api/client'

/** One paid module that needs attention, in a sentence anybody can act on (`GET /portal/licence/notice`). */
export interface LicenceWarning {
  module: 'codeindex' | 'artifacts' | 'fleet' | 'factory' | 'autopilot'
  state: 'valid' | 'grace' | 'read_only' | 'unlicensed'
  message: string
}

/**
 * The paid modules' licence, when something needs doing: expiring, lapsed
 * (90 days of warnings), read-only, or a licence file that is not valid.
 * Shown to everybody signed in, on every page. Nothing when all is well,
 * and nothing on a deployment that runs only the free base.
 *
 * The modules enforce their licence themselves; igni server only reports it.
 */
export function LicenceBanner() {
  const notice = useQuery({
    queryKey: ['licence-notice'],
    queryFn: () => client.get<{ warnings: LicenceWarning[] }>('/portal/licence/notice').then((r) => r.data),
    staleTime: 60 * 60 * 1000,
  })
  const warnings = notice.data?.warnings ?? []
  if (warnings.length === 0) return null
  return (
    <Stack gap="condensed" className="licence-banner">
      {warnings.map((warning) => (
        <Banner
          key={warning.module}
          variant={warning.state === 'grace' || warning.state === 'valid' ? 'warning' : 'critical'}
          title="Licence"
          hideTitle
          description={warning.message}
        />
      ))}
    </Stack>
  )
}
