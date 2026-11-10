/**
 * The server's sentence for a refused request, or `fallback`.
 *
 * The API answers refusals with `{detail: "..."}` written for a person —
 * "This is the only super admin. Make somebody else one first." — so a
 * page shows it as it is rather than inventing its own.
 */
export function refusalOf(error: unknown, fallback = 'That did not work. Try again.'): string {
  const detail = detailOf(error)
  return typeof detail === 'string' ? detail : fallback
}

/**
 * {@link refusalOf}, plus the shape a rejected form body comes back in.
 *
 * When the request body fails validation the API answers 422 with
 * `{detail: [{msg: "..."}, ...]}`; a form that posts free-typed fields
 * shows those messages joined rather than a generic fallback.
 */
export function formRefusalOf(error: unknown, fallback = 'That did not work. Try again.'): string {
  const detail = detailOf(error)
  if (!Array.isArray(detail)) return refusalOf(error, fallback)
  const messages = detail
    .map((item: { msg?: unknown } | null) => item?.msg)
    .filter((msg): msg is string => typeof msg === 'string' && msg !== '')
  return messages.length > 0 ? messages.join('. ') : fallback
}

function detailOf(error: unknown): unknown {
  return (error as { response?: { data?: { detail?: unknown } } } | null)?.response?.data?.detail
}
