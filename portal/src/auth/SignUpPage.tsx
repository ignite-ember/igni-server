import { useState } from 'react'
import { useNavigate, useSearchParams } from 'react-router-dom'
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
import { useAuth } from './useAuth'
import { IgniLogo } from '../components/IgniLogo'
import { OtherWaysIn, type OAuthProvider } from './OtherWaysIn'
import { useAuthMethods } from './useAuthMethods'
import './LoginPage.css'

export function SignUpPage() {
  useDocumentTitle('Sign Up', 'Create an account on this igni deployment.')
  const navigate = useNavigate()
  const [searchParams] = useSearchParams()
  const { login } = useAuth()

  const [step, setStep] = useState<'email' | 'code' | 'details'>('email')
  const [email, setEmail] = useState(searchParams.get('email') || '')
  const [code, setCode] = useState('')
  const [firstName, setFirstName] = useState('')
  const [lastName, setLastName] = useState('')
  const [password, setPassword] = useState('')
  const [confirmPassword, setConfirmPassword] = useState('')
  const [error, setError] = useState<string | null>(null)

  // After signup we land users on /welcome (CLI install).
  // Callers that explicitly pass ?returnTo=... still win — e.g. igni
  // bouncing the user back to /cli-auth after creating an account mid-flow.
  const returnTo = searchParams.get('returnTo') || '/welcome'

  const sendCodeMutation = useMutation({
    mutationFn: () => client.post('/send-sign-up-code', { email }),
    onSuccess: () => {
      setError(null)
      setStep('code')
    },
    onError: (err: unknown) => {
      const message =
        (err as { response?: { data?: { detail?: string } } })?.response?.data
          ?.detail || 'Failed to send code'
      setError(message)
    },
  })

  const signUpMutation = useMutation({
    mutationFn: () =>
      client.post<{ access_token: string }>('/sign-up', {
        first_name: firstName,
        last_name: lastName,
        email,
        code,
        password,
      }),
    onSuccess: (response) => {
      login(response.data.access_token)
      navigate(returnTo, { replace: true })
    },
    onError: (err: unknown) => {
      const data = (err as { response?: { data?: { detail?: unknown } } })
        ?.response?.data
      const detail = data?.detail
      let message = 'Failed to create account'
      if (typeof detail === 'string') {
        message = detail
      } else if (Array.isArray(detail) && detail.length > 0) {
        message = detail.map((e: { msg?: string }) => e.msg ?? '').filter(Boolean).join('. ')
      }
      setError(message)
    },
  })

  const PASSWORD_RULES = [
    { test: (p: string) => p.length >= 8, label: 'At least 8 characters' },
    { test: (p: string) => /[A-Z]/.test(p), label: 'One uppercase letter' },
    { test: (p: string) => /[a-z]/.test(p), label: 'One lowercase letter' },
    { test: (p: string) => /\d/.test(p), label: 'One digit' },
    { test: (p: string) => /[^A-Za-z0-9]/.test(p), label: 'One special character' },
  ]

  const passwordErrors = password
    ? PASSWORD_RULES.filter((r) => !r.test(password)).map((r) => r.label)
    : []

  const handleSendCode = (e: React.FormEvent) => {
    e.preventDefault()
    if (!email.trim()) return
    sendCodeMutation.mutate()
  }

  const handleVerifyCode = (e: React.FormEvent) => {
    e.preventDefault()
    if (!code.trim()) return
    setError(null)
    setStep('details')
  }

  const handleSignUp = (e: React.FormEvent) => {
    e.preventDefault()
    if (!firstName.trim() || !lastName.trim() || !password) return
    if (passwordErrors.length > 0) {
      setError('Password missing: ' + passwordErrors.join(', '))
      return
    }
    if (password !== confirmPassword) {
      setError('Passwords do not match')
      return
    }
    signUpMutation.mutate()
  }

  const isLoading = sendCodeMutation.isPending || signUpMutation.isPending

  // The buttons the server offers, as on the sign-in page. This page
  // used to show Google whatever the server said, which on a deployment
  // without Google (every air-gapped one) was a button to an error.
  const { data: methods } = useAuthMethods()

  const handleOAuth = (provider: OAuthProvider) => {
    const params = returnTo !== '/select-org'
      ? `?returnTo=${encodeURIComponent(returnTo)}`
      : ''
    window.location.href = `/v1/auth/${provider}${params}`
  }

  const handleSso = () => {
    const params = returnTo !== '/select-org'
      ? `?returnTo=${encodeURIComponent(returnTo)}`
      : ''
    window.location.href = `${methods?.sso.start_url ?? '/v1/auth/oidc'}${params}`
  }

  const headingText =
    step === 'email'
      ? 'Create your igni account'
      : step === 'code'
        ? 'Enter verification code'
        : 'Complete your profile'

  return (
    <Stack as="main" align="center" justify="center" className="login-page">
      <IgniLogo size={48} />

      <Heading as="h1" className="login-heading">
        {headingText}
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

        {step === 'email' && (
          <Stack gap="normal">
            {/* Email */}
            <form onSubmit={handleSendCode}>
              <Stack gap="normal">
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
                  {isLoading ? <Spinner size="small" /> : 'Send verification code'}
                </Button>
              </Stack>
            </form>

            <OtherWaysIn methods={methods} onOAuth={handleOAuth} onSso={handleSso} />
          </Stack>
        )}

        {step === 'code' && (
          <form onSubmit={handleVerifyCode}>
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

              <Button
                type="submit"
                variant="primary"
                block
                disabled={!code.trim()}
              >
                Verify
              </Button>
            </Stack>
          </form>
        )}

        {step === 'details' && (
          <form onSubmit={handleSignUp}>
            <Stack gap="normal">
              <FormControl>
                <FormControl.Label>First name</FormControl.Label>
                <TextInput
                  type="text"
                  value={firstName}
                  onChange={(e) => setFirstName(e.target.value)}
                  block
                  autoFocus
                  aria-label="First name"
                  contrast
                />
              </FormControl>

              <FormControl>
                <FormControl.Label>Last name</FormControl.Label>
                <TextInput
                  type="text"
                  value={lastName}
                  onChange={(e) => setLastName(e.target.value)}
                  block
                  aria-label="Last name"
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
                  validationStatus={password && passwordErrors.length > 0 ? 'error' : undefined}
                />
                {password && passwordErrors.length > 0 && (
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
                disabled={isLoading || !firstName.trim() || !lastName.trim() || !password || !confirmPassword}
              >
                {isLoading ? <Spinner size="small" /> : 'Create account'}
              </Button>
            </Stack>
          </form>
        )}
      </Stack>

      {/* Footer */}
      <Stack className="login-footer" align="center" padding="normal">
        {step === 'code' ? (
          <Text as="p" size="medium">
            Didn&apos;t receive a code?{' '}
            <Link
              as="button"
              onClick={() => sendCodeMutation.mutate()}
              muted={isLoading}
            >
              Resend
            </Link>
            {' \u00b7 '}
            <Link
              as="button"
              onClick={() => {
                setStep('email')
                setCode('')
                setError(null)
              }}
            >
              Use a different email
            </Link>
          </Text>
        ) : (
          <Text as="p" size="medium">
            Already have an account?{' '}
            <Link href="/login" inline>
              Sign in
            </Link>
          </Text>
        )}
      </Stack>
    </Stack>
  )
}
