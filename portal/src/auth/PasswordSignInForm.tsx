import { useState } from 'react'
import { Button, FormControl, Spinner, Stack, TextInput } from '@primer/react'
import { useMutation } from '@tanstack/react-query'
import client from '../api/client'
import { refusalOf } from '../api/refusal'

/** Email and password, posted to `/sign-in`. */
export function PasswordSignInForm({
  email,
  onEmailChange,
  onSignedIn,
  onRefused,
}: {
  email: string
  onEmailChange: (email: string) => void
  onSignedIn: (token: string) => void
  onRefused: (message: string) => void
}) {
  const [password, setPassword] = useState('')

  const signIn = useMutation({
    mutationFn: () => client.post<{ access_token: string }>('/sign-in', { email, password }),
    onSuccess: (response) => onSignedIn(response.data.access_token),
    onError: (err) => onRefused(refusalOf(err, 'Invalid email or password')),
  })

  const ready = email.trim() !== '' && password.trim() !== ''

  const submit = (e: React.FormEvent) => {
    e.preventDefault()
    if (ready) signIn.mutate()
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

        <FormControl>
          <FormControl.Label>Password</FormControl.Label>
          <TextInput
            type="password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            block
            aria-label="Password"
            contrast
          />
        </FormControl>

        <Button type="submit" variant="primary" block disabled={signIn.isPending || !ready}>
          {signIn.isPending ? <Spinner size="small" /> : 'Sign in'}
        </Button>
      </Stack>
    </form>
  )
}
