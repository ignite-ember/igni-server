/**
 * Every documentation link in the sidebar must lead somewhere.
 *
 * The resources section had four entries and three of them 404'd:
 * `docs/onboarding`, `docs/integrations` and `docs/codeindex`, all
 * written against a documentation set that was deleted and rebuilt from
 * source. Nothing connected the two, so the links rotted silently —
 * and a dead link in the signed-in sidebar is worse than one on a
 * marketing page, because the person clicking it has already decided to
 * trust the product.
 *
 * The documentation now lives in a separate repository (igni-site) and
 * is published at `https://ignite-ember.sh/docs`, so the manifest this
 * used to read is no longer here. `PUBLISHED_SLUGS` is the site's
 * article list at the time of the split. It is a copy and can go stale;
 * what it still catches is a typo in a slug, or a link to an article
 * that was never written. Renaming an article on the site means
 * updating this list and the link together.
 */

import { screen } from '@testing-library/react'
import { BookIcon } from '@primer/octicons-react'
import { describe, expect, it } from 'vitest'

import { renderWithProviders } from '../../../test/render'
import { Sidebar } from '../Sidebar'
import { DOCS_URL, resourceItems } from '../sidebarResources'

const PUBLISHED_SLUGS = [
  'installing-igni',
  'models-and-the-factory',
  'self-hosting-the-server',
  'server-configuration',
  'inference-backends',
  'administering-your-organisation',
  'running-the-documents-service',
  'licensing-and-evaluations',
  'offline-updates',
  'configuration-and-precedence',
  'project-context-and-memory',
  'permissions-and-safety',
  'agents-and-orchestration',
  'slash-commands-and-skills',
  'tools-mcp-and-language-servers',
  'hooks-workflows-and-scheduled-tasks',
  'artifacts',
  'codeindex',
  'fleet',
  'building-extensions',
]

const articleLinks = resourceItems
  .map((item) => item.href)
  .filter((href) => href.startsWith(`${DOCS_URL}/`))

describe('the sidebar resource links', () => {
  it('point at the published documentation', () => {
    expect(DOCS_URL).toBe('https://ignite-ember.sh/docs')
    for (const item of resourceItems) {
      expect(item.href === DOCS_URL || item.href.startsWith(`${DOCS_URL}/`), item.href).toBe(true)
    }
  })

  it('include some article links', () => {
    // Without this the rule below passes vacuously on an empty list.
    expect(articleLinks.length).toBeGreaterThan(0)
  })

  it.each(articleLinks)('%s is an article the site publishes', (href) => {
    const slug = href.slice(`${DOCS_URL}/`.length)

    expect(PUBLISHED_SLUGS).toContain(slug)
  })

  it('points the index entry at the documentation root', () => {
    expect(resourceItems[0].href).toBe(DOCS_URL)
    expect(resourceItems[0].icon).toBe(BookIcon)
  })

  it('the real sidebar renders them as links out, not router paths', async () => {
    // The portal has no `/docs` route any more — a router `Link` to
    // `/docs` would land on the catch-all and bounce to `/dashboard`.
    renderWithProviders(<Sidebar />)

    for (const item of resourceItems) {
      expect(await screen.findByRole('link', { name: item.label })).toHaveAttribute(
        'href',
        item.href,
      )
    }
  })
})
