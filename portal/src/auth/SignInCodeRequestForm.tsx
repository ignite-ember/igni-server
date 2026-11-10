import { Button, FormControl, Spinner, Stack, TextInput } from '@primer/react'

/**
 * The email half of signing in with a one-time code. The request itself
 * belongs to the page, because the verify step's "Resend" sends it too.
 */
export function SignInCodeRequestForm({
  email,
  onEmailChange,
  onRequest,
  sending,
}: {
  email: string
  onEmailChange: (email: string) => void
  onRequest: () => void
  sending: boolean
}) {
  const ready = email.trim() !== ''

  const submit = (e: React.FormEvent) => {
    e.preventDefault()
    if (ready) onRequest()
  }

  return (
    <form onSubmit={submit}>
      <Stack gap="normal">
        <FormControl>
          <FormControl.Label>Email address</FormControl.Label>
          <TextInput
            type="email"
            value={email}
            onChange={(e) => onEmailChange(e.target.value)}
            block
            autoFocus
            aria-label="Email address"
            contrast
          />
        </FormControl>

        <Button type="submit" variant="primary" block disabled={sending || !ready}>
          {sending ? <Spinner size="small" /> : 'Send sign-in code'}
        </Button>
      </Stack>
    </form>
  )
}
