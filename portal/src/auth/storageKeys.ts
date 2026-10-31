/**
 * The two things the portal keeps in `localStorage`, named in one place.
 *
 * They used to be spelled `ember_token` and `ember_active_org`, written
 * as bare string literals in a dozen files. The product is igni, so the
 * keys were renamed — and a key rename in `localStorage` is a silent
 * logout, because the browser still holds the old entry under a name
 * nothing reads any more.
 *
 * `adoptLegacyKeys()` moves the old entries across once, at startup,
 * before anything reads them. It is deliberately a copy-then-remove
 * rather than a read-through fallback: a fallback has to stay in every
 * reader forever, whereas this runs once and the old names are gone.
 *
 * Safe to delete this module's legacy half once no browser can still be
 * holding a pre-rename session.
 */

export const TOKEN_KEY = 'igni_token'
export const ACTIVE_ORG_KEY = 'igni_active_org'

const LEGACY: Array<[string, string]> = [
  ['ember_token', TOKEN_KEY],
  ['ember_active_org', ACTIVE_ORG_KEY],
]

/** Carry a pre-rename session over to the current key names. */
export function adoptLegacyKeys(): void {
  for (const [old, current] of LEGACY) {
    const value = localStorage.getItem(old)
    if (value === null) continue
    // The current key wins: if both exist, the old one is a leftover
    // from a session that has already been replaced.
    if (localStorage.getItem(current) === null) localStorage.setItem(current, value)
    localStorage.removeItem(old)
  }
}
