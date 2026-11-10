/**
 * What a new password has to contain, as the server enforces it.
 *
 * Sign-up and password reset both check these before they send, so the
 * person sees what is missing as they type rather than after a refusal.
 */
interface PasswordRule {
  readonly label: string
  readonly test: (password: string) => boolean
}

export const PASSWORD_RULES: readonly PasswordRule[] = [
  { label: 'At least 8 characters', test: (p) => p.length >= 8 },
  { label: 'One uppercase letter', test: (p) => /[A-Z]/.test(p) },
  { label: 'One lowercase letter', test: (p) => /[a-z]/.test(p) },
  { label: 'One digit', test: (p) => /\d/.test(p) },
  { label: 'One special character', test: (p) => /[^A-Za-z0-9]/.test(p) },
]

/** The labels of the rules `password` breaks; none for an empty one. */
export function missingPasswordRules(password: string): string[] {
  if (!password) return []
  return PASSWORD_RULES.filter((rule) => !rule.test(password)).map((rule) => rule.label)
}
