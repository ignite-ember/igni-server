/**
 * The public key an app generated, as the admin pastes it: one JWK, or a
 * JWK set. Anything with a private part (`d`) is refused here before the
 * server refuses it too — a private key must never be typed into a page.
 */
export function parsePublicJwks(text: string): { keys: Record<string, unknown>[] } | string {
  let parsed: unknown
  try {
    parsed = JSON.parse(text)
  } catch {
    return 'Paste the public key as JSON: one JWK, or {"keys": [...]}.'
  }
  const keys = (parsed as { keys?: unknown }).keys ?? [parsed]
  if (!Array.isArray(keys) || keys.length === 0 || keys.some((k) => typeof k !== 'object' || k === null)) {
    return 'Paste the public key as JSON: one JWK, or {"keys": [...]}.'
  }
  if (keys.some((k) => 'd' in (k as object))) {
    return 'That is a private key. Paste only the public half; the private key stays with the app.'
  }
  return { keys: keys as Record<string, unknown>[] }
}
