/**
 * The sidebar's "Resources" rows, in their own module so a test can
 * check them.
 *
 * The documentation is published on the public site, not served by the
 * portal: it moved out with the landing page on 2026-10-31, into its
 * own repository (igni-site). So these are absolute links to
 * `https://ignite-ember.sh/docs`, and an air-gapped deployment's
 * sidebar points at a host it cannot reach — the same as any other
 * outbound link, and no worse than shipping a stale copy of the docs
 * inside every portal.
 *
 * Three of the original four pointed at documentation that no longer
 * existed — `docs/onboarding`, `docs/integrations` and `docs/codeindex`.
 * A dead link in the signed-in sidebar is worse than one on a marketing
 * page: the person clicking it has already decided to trust the product.
 * `__tests__/Sidebar.docLinks.test.tsx` checks the slugs.
 *
 * It lives here rather than in `Sidebar.tsx` because exporting a
 * constant from a component module breaks fast refresh
 * (`react-refresh/only-export-components`), which the portal's lint
 * gate treats as an error.
 */

import { BookIcon, PlugIcon, RocketIcon } from '@primer/octicons-react'

/** Where the documentation is published. */
export const DOCS_URL = 'https://ignite-ember.sh/docs'

export const resourceItems = [
  { label: 'Documentation', href: DOCS_URL, icon: BookIcon },
  { label: 'Self-hosting', href: `${DOCS_URL}/self-hosting-the-server`, icon: RocketIcon },
  { label: 'Inference backends', href: `${DOCS_URL}/inference-backends`, icon: PlugIcon },
]
