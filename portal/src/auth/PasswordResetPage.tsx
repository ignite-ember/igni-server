import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { useDocumentTitle } from '../hooks/useDocumentTitle'
import {
  Banner,
  Button,
  FormControl,
  Heading,
  Link,
  Spinner,
  Stack,
  Text,
  TextInput,
} from '@primer/react'
import { useMutation } from '@tanstack/react-query'
import client from '../api/client'
import { formRefusalOf, refusalOf } from '../api/refusal'
import { IgniLogo } from '../components/IgniLogo'
import { missingPasswordRules } from './passwordRules'
import './LoginPage.css'

export function PasswordResetPage() {
  useDocumentTitle('Reset Password', 'Reset your igni account password.')
  const navigate = useNavigate()

  const [step, setStep] = useState<'email' | 'code' | 'password'>('email')
  const [email, setEmail] = useState('')
  const [code, setCode] = useState('')
  const [newPassword, setNewPassword] = useState('')
  const [confirmPassword, setConfirmPassword] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [success, setSuccess] = useState<string | null>(null)

  const sendResetCode = useMutation({
    mutationFn: () => client.post('/send-reset-password-code', { email }),
    onSuccess: () => {
      setError(null)
      setStep('code')
    },
    onError: (err: unknown) => {
      setError(refusalOf(err, 'Failed to send reset code'))
    },
  })

  const resetPassword = useMutation({
    mutationFn: () =>
      client.post('/reset-password', {
        email,
        code,
        new_password: newPassword,
      }),
    onSuccess: (response) => {
      setError(null)
      setSuccess(response.data.message || 'Password reset successfully')
      setTimeout(() => navigate('/login', { replace: true }), 2000)
    },
    onError: (err: unknown) => {
      setError(formRefusalOf(err, 'Failed to reset password'))
    },
  })

  const passwordErrors = missingPasswordRules(newPassword)

  const handleSendCode = (e: React.FormEvent) => {
    e.preventDefault()
    if (!email.trim()) return
    sendResetCode.mutate()
  }

  const handleSubmitCode = (e: React.FormEvent) => {
    e.preventDefault()
    if (!code.trim()) return
    setError(null)
    setStep('password')
  }

  const handleResetPassword = (e: React.FormEvent) => {
    e.preventDefault()
    if (passwordErrors.length > 0) {
      setError('Password missing: ' + passwordErrors.join(', '))
      return
    }
    if (newPassword !== confirmPassword) {
      setError('Passwords do not match')
      return
    }
    resetPassword.mutate()
  }

  const isLoading = sendResetCode.isPending || resetPassword.isPending

  const heading =
    step === 'email'
      ? 'Reset your password'
      : step === 'code'
        ? 'Enter reset code'
        : 'Set new password'

  return (
    <Stack align="center" justify="center" className="login-page">
      <IgniLogo size={48} />

      <Heading as="h1" className="login-heading">
        {heading}
      </Heading>

      {/* Main card */}
      <Stack className="login-card" gap="normal" padding="normal">
        {error && (
          <Banner
            variant="critical"
            title={error}
            onDismiss={() => setError(null)}
          />
        )}

        {success && (
          <Banner variant="success" title={success} />
        )}

        {step === 'email' && (
          <form onSubmit={handleSendCode}>
            <Stack gap="normal">
              <Text as="p" size="medium" className="login-hint">
                Enter the email address associated with your account and
                we&apos;ll send you a code to reset your password.
              </Text>

              <FormControl>
                <FormControl.Label>Email address</FormControl.Label>
                <TextInput
                  type="email"
                  value={email}
                  onChange={(e) => setEmail(e.target.value)}
                  block
                  autoFocus
                  aria-label="Email address"
                  contrast
                />
              </FormControl>

              <Button
                type="submit"
                variant="primary"
                block
                disabled={isLoading || !email.trim()}
              >
                {isLoading ? <Spinner size="small" /> : 'Send reset code'}
              </Button>
            </Stack>
          </form>
        )}

        {step === 'code' && (
          <form onSubmit={handleSubmitCode}>
            <Stack gap="normal">
              <Text as="p" size="medium" className="login-hint">
                We sent a reset code to{' '}
                <Text weight="semibold">{email}</Text>.
              </Text>

              <FormControl>
                <FormControl.Label>Reset code</FormControl.Label>
                <TextInput
                  type="text"
                  placeholder="XXXXXX"
                  value={code}
                  onChange={(e) => setCode(e.target.value)}
                  block
                  autoFocus
                  autoComplete="one-time-code"
                  aria-label="Reset code"
                  contrast
                  className="login-code-input"
                />
              </FormControl>

              <Button
                type="submit"
                variant="primary"
                block
                disabled={!code.trim()}
              >
                Continue
              </Button>
            </Stack>
          </form>
        )}

        {step === 'password' && !success && (
          <form onSubmit={handleResetPassword}>
            <Stack gap="normal">
              <FormControl>
                <FormControl.Label>New password</FormControl.Label>
                <TextInput
                  type="password"
                  value={newPassword}
                  onChange={(e) => setNewPassword(e.target.value)}
                  block
                  autoFocus
                  aria-label="New password"
                  contrast
                  validationStatus={newPassword && passwordErrors.length > 0 ? 'error' : undefined}
                />
                {newPassword && passwordErrors.length > 0 && (
                  <FormControl.Validation variant="error">
                    Missing: {passwordErrors.join(', ')}
                  </FormControl.Validation>
                )}
              </FormControl>

              <FormControl>
                <FormControl.Label>Confirm password</FormControl.Label>
                <TextInput
                  type="password"
                  value={confirmPassword}
                  onChange={(e) => setConfirmPassword(e.target.value)}
                  block
                  aria-label="Confirm password"
                  contrast
                />
              </FormControl>

              <Button
                type="submit"
                variant="primary"
                block
                disabled={isLoading || !newPassword || !confirmPassword}
              >
                {isLoading ? <Spinner size="small" /> : 'Reset password'}
              </Button>
            </Stack>
          </form>
        )}

        {success && (
          <Text as="p" size="medium" className="login-hint">
            <Link href="/login" inline>
              Back to sign in
            </Link>
          </Text>
        )}
      </Stack>

      {/* Footer */}
      <Stack className="login-footer" align="center" padding="normal">
        <Text as="p" size="medium">
          Remember your password?{' '}
          <Link href="/login" inline>
            Sign in
          </Link>
        </Text>
      </Stack>
    </Stack>
  )
}
