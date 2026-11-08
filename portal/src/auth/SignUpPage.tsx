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
import { formRefusalOf, refusalOf } from '../api/refusal'
import { useAuth } from './useAuth'
import { IgniLogo } from '../components/IgniLogo'
import { OtherWaysIn } from './OtherWaysIn'
import { safeReturnTo } from './returnTo'
import { SignUpDetailsForm, type SignUpDetails } from './SignUpDetailsForm'
import { useAuthMethods } from './useAuthMethods'
import './LoginPage.css'

type Step = 'email' | 'code' | 'details'

const HEADINGS: Record<Step, string> = {
  email: 'Create your igni account',
  code: 'Enter verification code',
  details: 'Complete your profile',
}

export function SignUpPage() {
  useDocumentTitle('Sign Up', 'Create an account on this igni deployment.')
  const navigate = useNavigate()
  const [searchParams] = useSearchParams()
  const { login } = useAuth()

  const [step, setStep] = useState<Step>('email')
  const [email, setEmail] = useState(searchParams.get('email') || '')
  const [code, setCode] = useState('')
  const [error, setError] = useState<string | null>(null)

  // After signup we land users on /welcome (CLI install).
  // Callers that explicitly pass ?returnTo=... still win — e.g. igni
  // bouncing the user back to /cli-auth after creating an account mid-flow —
  // but only a same-origin path does; see returnTo.ts.
  const returnTo = safeReturnTo(searchParams.get('returnTo') || '/welcome')

  const sendCodeMutation = useMutation({
    mutationFn: () => client.post('/send-sign-up-code', { email }),
    onSuccess: () => {
      setError(null)
      setStep('code')
    },
    onError: (err: unknown) => {
      setError(refusalOf(err, 'Failed to send code'))
    },
  })

  const signUpMutation = useMutation({
    mutationFn: (details: SignUpDetails) =>
      client.post<{ access_token: string }>('/sign-up', {
        first_name: details.firstName,
        last_name: details.lastName,
        email,
        code,
        password: details.password,
      }),
    onSuccess: (response) => {
      login(response.data.access_token)
      navigate(returnTo, { replace: true })
    },
    onError: (err: unknown) => {
      setError(formRefusalOf(err, 'Failed to create account'))
    },
  })

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

  const isLoading = sendCodeMutation.isPending || signUpMutation.isPending

  // The buttons the server offers, as on the sign-in page. This page
  // used to show Google whatever the server said, which on a deployment
  // without Google (every air-gapped one) was a button to an error.
  const { data: methods } = useAuthMethods()

  return (
    <Stack as="main" align="center" justify="center" className="login-page">
      <IgniLogo size={48} />

      <Heading as="h1" className="login-heading">
        {HEADINGS[step]}
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

            <OtherWaysIn methods={methods} returnTo={returnTo} />
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
          <SignUpDetailsForm
            pending={signUpMutation.isPending}
            onSubmit={(details) => signUpMutation.mutate(details)}
            onInvalid={setError}
          />
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
