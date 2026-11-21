import { Banner, Stack } from '@primer/react'
import { useLicenceNotice } from '../../hooks/useLicenceNotice'

/**
 * The paid modules' licence, when something needs doing: expiring, lapsed
 * (90 days of warnings), read-only, or a licence file that is not valid.
 * Shown to everybody signed in, on every page. Nothing when all is well,
 * and nothing on a deployment that runs only the free base. A system
 * clock behind a time the deployment has already seen is said too: the
 * licence is evaluated at that time until somebody fixes the clock.
 *
 * The modules enforce their licence themselves; igni server only reports it.
 */
export function LicenceBanner() {
  const notice = useLicenceNotice()
  const warnings = notice.data?.warnings ?? []
  const clock = notice.data?.clock
  if (warnings.length === 0 && !clock) return null
  return (
    <Stack gap="condensed" className="licence-banner">
      {clock && <Banner key="clock" variant="warning" title="Licence clock" hideTitle description={clock} />}
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
