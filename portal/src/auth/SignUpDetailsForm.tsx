import { useState } from 'react'
import { Button, FormControl, Spinner, Stack, TextInput } from '@primer/react'
import { missingPasswordRules } from './passwordRules'

/** The profile half of a sign-up, once the address is verified. */
export interface SignUpDetails {
  firstName: string
  lastName: string
  password: string
}

interface DetailsDraft extends SignUpDetails {
  confirmPassword: string
}

const EMPTY_DRAFT: DetailsDraft = { firstName: '', lastName: '', password: '', confirmPassword: '' }

/**
 * Name and password, checked here before anything is sent.
 *
 * A broken password rule or a mismatched confirmation goes to `onInvalid`
 * so the page shows it in the same banner as a server refusal.
 */
export function SignUpDetailsForm({
  pending,
  onSubmit,
  onInvalid,
}: {
  pending: boolean
  onSubmit: (details: SignUpDetails) => void
  onInvalid: (message: string) => void
}) {
  const [draft, setDraft] = useState<DetailsDraft>(EMPTY_DRAFT)
  const passwordErrors = missingPasswordRules(draft.password)
  const complete =
    draft.firstName.trim() !== '' && draft.lastName.trim() !== '' && draft.password !== '' && draft.confirmPassword !== ''

  const field = (name: keyof DetailsDraft) => ({
    value: draft[name],
    onChange: (e: React.ChangeEvent<HTMLInputElement>) => setDraft((d) => ({ ...d, [name]: e.target.value })),
  })

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault()
    if (!draft.firstName.trim() || !draft.lastName.trim() || !draft.password) return
    if (passwordErrors.length > 0) {
      onInvalid('Password missing: ' + passwordErrors.join(', '))
      return
    }
    if (draft.password !== draft.confirmPassword) {
      onInvalid('Passwords do not match')
      return
    }
    onSubmit({ firstName: draft.firstName, lastName: draft.lastName, password: draft.password })
  }

  return (
    <form onSubmit={handleSubmit}>
      <Stack gap="normal">
        <FormControl>
          <FormControl.Label>First name</FormControl.Label>
          <TextInput type="text" {...field('firstName')} block autoFocus aria-label="First name" contrast />
        </FormControl>

        <FormControl>
          <FormControl.Label>Last name</FormControl.Label>
          <TextInput type="text" {...field('lastName')} block aria-label="Last name" contrast />
        </FormControl>

        <FormControl>
          <FormControl.Label>Password</FormControl.Label>
          <TextInput
            type="password"
            {...field('password')}
            block
            aria-label="Password"
            contrast
            validationStatus={passwordErrors.length > 0 ? 'error' : undefined}
          />
          {passwordErrors.length > 0 && (
            <FormControl.Validation variant="error">Missing: {passwordErrors.join(', ')}</FormControl.Validation>
          )}
        </FormControl>

        <FormControl>
          <FormControl.Label>Confirm password</FormControl.Label>
          <TextInput type="password" {...field('confirmPassword')} block aria-label="Confirm password" contrast />
        </FormControl>

        <Button type="submit" variant="primary" block disabled={pending || !complete}>
          {pending ? <Spinner size="small" /> : 'Create account'}
        </Button>
      </Stack>
    </form>
  )
}
