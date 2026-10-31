import { useEffect } from 'react'

const BASE_TITLE = 'igni'

/**
 * Set the tab title, and the meta description when one is given.
 *
 * This used to write Open Graph, Twitter and canonical tags pointing at
 * `https://ignite-ember.sh` — which was the public site's concern while
 * the site and the portal were one bundle. A self-hosted portal is not
 * that host, so naming it as the canonical address of a customer's
 * dashboard was wrong. The site, and its social tags, live in their own
 * repository now.
 */
export function useDocumentTitle(title?: string, description?: string) {
  useEffect(() => {
    document.title = title ? `${title} — ${BASE_TITLE}` : BASE_TITLE

    if (description) {
      let el = document.querySelector('meta[name="description"]')
      if (!el) {
        el = document.createElement('meta')
        el.setAttribute('name', 'description')
        document.head.appendChild(el)
      }
      el.setAttribute('content', description)
    }

    return () => {
      document.title = BASE_TITLE
    }
  }, [title, description])
}
