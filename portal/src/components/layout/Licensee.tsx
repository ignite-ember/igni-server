import { useLicenceNotice } from '../../hooks/useLicenceNotice'

/**
 * "Licensed to Example Bank N.V. · IE-2026-0042", quietly, at the foot of
 * the sidebar, for everybody signed in.
 *
 * Ignite Ember protects its paid modules with a signed licence, a
 * contract and traceability rather than DRM. This is the traceability a
 * person sees: a deployment copied or shared beyond its contract names
 * its licensee on every page, and a screenshot of it says whose it is.
 *
 * Nothing at all without a verified licence — the free base, or a file
 * that does not verify (the banner says so) — which is also what keeps
 * the visual baselines, recorded with no licence, unchanged.
 */
export function Licensee() {
  const notice = useLicenceNotice()
  const customer = notice.data?.customer
  const licenceId = notice.data?.licence_id
  if (!customer || !licenceId) return null
  return (
    <p className="sidebar-licensee">
      Licensed to {customer} · <span className="sidebar-licensee-id">{licenceId}</span>
    </p>
  )
}
