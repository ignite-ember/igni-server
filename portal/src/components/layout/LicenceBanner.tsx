import { Banner, Stack } from '@primer/react'
import { useLicenceNotice } from '../../hooks/useLicenceNotice'

/**
 * The paid modules' licence, when something needs doing: expiring, lapsed
 * (90 days of warnings), read-only, or a licence file that is not valid.
 * Shown to everybody signed in, on every page. Nothing when all is well,
 * and nothing on a deployment that runs only the free base.
 *
 * The modules enforce their licence themselves; igni server only reports it.
 */
export function LicenceBanner() {
  const notice = useLicenceNotice()
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
