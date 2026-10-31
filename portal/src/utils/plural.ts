/**
 * Counting things without saying "1 members".
 *
 * F132. `{members.length} members` and `{total} people` both shipped,
 * on the two pages a fresh deployment shows first — where the count is
 * exactly one. Two instances of one missing helper rather than two
 * typos, which is why this exists instead of two ternaries.
 *
 * English plurals are irregular often enough that the caller has to be
 * able to say so: "person"/"people" is not a suffix rule. The default
 * is `+ 's'` because most of the portal's nouns are regular.
 */

/** `3` + `member` → `"3 members"`. `1` + `member` → `"1 member"`. */
export function countOf(
  n: number,
  singular: string,
  plural = `${singular}s`,
): string {
  return `${n.toLocaleString()} ${n === 1 ? singular : plural}`
}

/** The noun alone, for when the number is rendered separately. */
export function pluralise(
  n: number,
  singular: string,
  plural = `${singular}s`,
): string {
  return n === 1 ? singular : plural
}
