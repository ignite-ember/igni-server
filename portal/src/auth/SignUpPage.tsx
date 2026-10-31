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
import './LoginPage.css'

/* ── Inline SVG icons for providers not in Octicons ── */

function GoogleIcon() {
  return (
    <svg width="20" height="20" viewBox="0 0 24 24" aria-hidden="true">
      <path d="M22.56 12.25c0-.78-.07-1.53-.2-2.25H12v4.26h5.92a5.06 5.06 0 0 1-2.2 3.32v2.77h3.57c2.08-1.92 3.28-4.74 3.28-8.1z" fill="#4285F4" />
      <path d="M12 23c2.97 0 5.46-.98 7.28-2.66l-3.57-2.77c-.98.66-2.23 1.06-3.71 1.06-2.86 0-5.29-1.93-6.16-4.53H2.18v2.84C3.99 20.53 7.7 23 12 23z" fill="#34A853" />
      <path d="M5.84 14.09c-.22-.66-.35-1.36-.35-2.09s.13-1.43.35-2.09V7.07H2.18A11.96 11.96 0 0 0 1 12c0 1.93.46 3.77 1.18 5.07l3.66-2.98z" fill="#FBBC05" />
      <path d="M12 5.38c1.62 0 3.06.56 4.21 1.64l3.15-3.15C17.45 2.09 14.97 1 12 1 7.7 1 3.99 3.47 2.18 7.07l3.66 2.84c.87-2.6 3.3-4.53 6.16-4.53z" fill="#EA4335" />
    </svg>
  )
}


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

  const handleOAuth = (provider: 'google') => {
    const params = returnTo !== '/select-org'
      ? `?returnTo=${encodeURIComponent(returnTo)}`
      : ''
    window.location.href = `/v1/auth/${provider}${params}`
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

            {/* Divider */}
            <Stack direction="horizontal" align="center" gap="normal" className="login-divider-row">
              <span className="login-divider-line" />
              <Text size="small" className="login-divider-text">Or continue with</Text>
              <span className="login-divider-line" />
            </Stack>

            {/* Social auth */}
            <Stack gap="condensed">
              <Button
                block
                leadingVisual={GoogleIcon}
                onClick={() => handleOAuth('google')}
              >
                Google
              </Button>
            </Stack>
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
