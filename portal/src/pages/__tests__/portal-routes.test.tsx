/**
 * The portal is the portal: its `/` opens the dashboard, and the public
 * site's pages are not here.
 *
 * Until 2026-10-31 this bundle was also the public site, behind a
 * `VITE_PUBLIC_SITE=1` build flag — the landing page with its prices
 * and booking links, the product pages and the documentation. They
 * moved to their own repository (igni-site) before the server was
 * published, along with the `/old` and `/alt` redirects (DEC-12) and
 * the tests for them. What is asserted here is that nothing of the site
 * came back:
 *
 * 1. `/` goes to the portal, never to a sales page.
 * 2. `/docs` is not routed: the documentation is published at
 *    ignite-ember.sh, and the sidebar links there.
 * 3. The route table names no site page and reads no site flag.
 *
 * `App` is rendered directly rather than through `renderWithProviders`,
 * because it brings its own `BrowserRouter` — two routers nested is an
 * invariant violation, and the point is to exercise the real route
 * table rather than a copy of it that could disagree.
 */
import { beforeEach, describe, expect, test, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'

import App from '../../App'

// `import.meta.url` is not a file: URL under the jsdom environment, so the
// path is resolved from the vitest working directory (`portal/`) instead.
const APP_SOURCE = readFileSync(resolve(process.cwd(), 'src/App.tsx'), 'utf8')

describe('a self-hosted install', () => {
  beforeEach(() => {
    window.history.pushState({}, '', '/')
  })

  test('opens on its own portal, not on a sales page', async () => {
    render(<App />)

    await vi.waitFor(() => expect(window.location.pathname).not.toBe('/'))
    expect(screen.queryByRole('heading', { name: /inside your network/i })).not.toBeInTheDocument()
  })

  test('does not serve the documentation itself', async () => {
    window.history.pushState({}, '', '/docs')
    render(<App />)

    await vi.waitFor(() => expect(window.location.pathname).not.toMatch(/^\/docs/))
  })
})

describe('the route table itself', () => {
  test('names none of the public site pages', () => {
    for (const page of ['LandingPage', 'ProductPage', 'DocsPage', 'DocArticlePage', 'DownloadPage']) {
      expect(APP_SOURCE, page).not.toContain(page)
    }
  })

  test('has no public-site build flag', () => {
    expect(APP_SOURCE).not.toContain('PUBLIC_SITE')
  })

  test('the site pages are gone from the repository', () => {
    const pages = import.meta.glob('../*.tsx')

    for (const page of ['LandingPage', 'LandingPageOld', 'ProductPage', 'DocsPage', 'DocArticlePage']) {
      expect(Object.keys(pages)).not.toContain(`../${page}.tsx`)
    }
    // Guards the glob: a pattern that matched nothing would pass above.
    expect(Object.keys(pages)).toContain('../DashboardPage.tsx')
  })
})
