/**
 * The routes the visual baselines cover, shared by the recorder and
 * the comparison so the two cannot drift apart.
 *
 * The landing page (`01-landing`) and the docs index (`16-docs`) were
 * here until they moved to the public site's own repository (igni-site)
 * on 2026-10-31; their baselines and fixtures went with them.
 */

export type VisualRoute = {
  /** Path to visit. */
  path: string
  /** File-name stem for the fixture and the baselines. */
  name: string
  /** Whether the page needs a session seeded before it will render. */
  signedIn: boolean
}

export const VISUAL_ROUTES: readonly VisualRoute[] = [
  { path: '/login', name: '02-login', signedIn: false },
  { path: '/signup', name: '03-signup', signedIn: false },
  { path: '/dashboard', name: '04-dashboard', signedIn: true },
  { path: '/team', name: '06-team', signedIn: true },
  { path: '/groups', name: '07-groups', signedIn: true },
  { path: '/groups/new', name: '08-group-new', signedIn: true },
  { path: '/inference', name: '10-inference', signedIn: true },
  { path: '/compliance', name: '13-compliance', signedIn: true },
  { path: '/settings', name: '14-settings', signedIn: true },
  { path: '/welcome', name: '15-welcome', signedIn: true },
]
