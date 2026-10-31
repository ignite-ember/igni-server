/**
 * The server's sentence for a refused request, or `fallback`.
 *
 * The API answers refusals with `{detail: "..."}` written for a person —
 * "This is the only super admin. Make somebody else one first." — so a
 * page shows it as it is rather than inventing its own.
 */
export function refusalOf(error: unknown, fallback = 'That did not work. Try again.'): string {
  const detail = (error as { response?: { data?: { detail?: unknown } } } | null)?.response?.data?.detail
  return typeof detail === 'string' ? detail : fallback
}
