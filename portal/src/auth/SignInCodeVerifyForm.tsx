import { useState } from 'react'
import { Button, FormControl, Spinner, Stack, Text, TextInput } from '@primer/react'
import { useMutation } from '@tanstack/react-query'
import client from '../api/client'
import { refusalOf } from '../api/refusal'

/** The code half of signing in with a one-time code, posted to `/sign-in-with-code`. */
export function SignInCodeVerifyForm({
  email,
  resending,
  onSignedIn,
  onRefused,
}: {
  email: string
  /** A fresh code is on its way, so the one typed here is about to be stale. */
  resending: boolean
  onSignedIn: (token: string) => void
  onRefused: (message: string) => void
}) {
  const [code, setCode] = useState('')

  const verify = useMutation({
    mutationFn: () =>
      client.post<{ access_token: string }>('/sign-in-with-code', { email, code }),
    onSuccess: (response) => onSignedIn(response.data.access_token),
    onError: (err) => onRefused(refusalOf(err, 'Invalid code')),
  })

  const busy = verify.isPending || resending
  const ready = code.trim() !== ''

  const submit = (e: React.FormEvent) => {
    e.preventDefault()
    if (ready) verify.mutate()
  }

  return (
    <form onSubmit={submit}>
      <Stack gap="normal">
        <Text as="p" size="medium" className="login-hint">
          We sent a verification code to{' '}
          <Text weight="semibold">{email}</Text>.
        </Text>

        <FormControl>
          <FormControl.Label>Verification code</FormControl.Label>
          <TextInput
            type="text"
            placeholder="XXXXXX"
            value={code}
            onChange={(e) => setCode(e.target.value)}
            block
            autoFocus
            autoComplete="one-time-code"
            aria-label="Verification code"
            contrast
            className="login-code-input"
          />
        </FormControl>

        <Button type="submit" variant="primary" block disabled={busy || !ready}>
          {busy ? <Spinner size="small" /> : 'Verify'}
        </Button>
      </Stack>
    </form>
  )
}
