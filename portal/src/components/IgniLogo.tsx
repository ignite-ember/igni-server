import igniIconUrl from '../assets/igni-icon.svg'

/**
 * igni brand logo — the cat on its coral tile, matching the favicon.
 * Used on login page, header, and landing.
 */
export function IgniLogo({ size = 48 }: { size?: number }) {
  return <img src={igniIconUrl} width={size} height={size} alt="igni" style={{ display: 'block' }} />
}
