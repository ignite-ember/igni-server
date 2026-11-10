import { useCallback, useState } from 'react'
import { useNavigate, useSearchParams } from 'react-router-dom'
import { Banner, Heading, Link, Stack, Text } from '@primer/react'
import { useMutation } from '@tanstack/react-query'
import client from '../api/client'
import { refusalOf } from '../api/refusal'
import { useDocumentTitle } from '../hooks/useDocumentTitle'
import { IgniLogo } from '../components/IgniLogo'
import { useAuth } from './useAuth'
import { safeReturnTo } from './returnTo'
import { OtherWaysIn } from './OtherWaysIn'
import { useAuthMethods } from './useAuthMethods'
import { useSignInCodeExchange } from './useSignInCodeExchange'
import { PasswordSignInForm } from './PasswordSignInForm'
import { SignInCodeRequestForm } from './SignInCodeRequestForm'
import { SignInCodeVerifyForm } from './SignInCodeVerifyForm'
import './LoginPage.css'

/**
 * Which form the card shows. One value rather than a method and a step,
 * which between them could name a combination no screen draws.
 */
type View = 'password' | 'request-code' | 'verify-code'

export function LoginPage() {
  useDocumentTitle('Sign In', 'Sign in to igni with email, password, Google or single sign-on.')
  const navigate = useNavigate()
  const [searchParams] = useSearchParams()
  const { login } = useAuth()

  const [view, setView] = useState<View>('password')
  const [email, setEmail] = useState('')

  // Where to go after login — /cli-auth?port=... for CLI flow, /dashboard
  // for the portal. Filtered: this value survives a round trip through
  // an identity provider and is then handed to `navigate()`, so
  // `?returnTo=//evil.example.com` would be an open redirect off the
  // back of a real sign-in. See returnTo.ts for what the router already
  // refuses and why this does not rely on that.
  const returnTo = safeReturnTo(searchParams.get('returnTo'))

  const signedIn = useCallback(
    (token: string) => {
      login(token)
      navigate(returnTo, { replace: true })
    },
    [login, navigate, returnTo],
  )

  // A one-time code in the query string means a provider just sent the
  // browser back here.
  const exchangeError = useSignInCodeExchange(searchParams.get('code') || null, signedIn)

  // Which methods this deployment offers. Without this the page showed
  // hardcoded providers whether or not the server accepted them,
  // and never showed SSO at all.
  const { data: methods } = useAuthMethods()

  // Two kinds of error with one banner. What the forms produce is state;
  // what the code exchange or an OAuth/SSO redirect hands back is derived
  // during render, because copying it into state with an effect is the
  // cascading render React warns about. Dismissing the banner clears the
  // first and hides the second; so does moving to another form or sending
  // a code, or a stale message would follow the user onto a screen it is
  // not about. What is hidden is the message on screen at the time, so an
  // exchange that fails later still gets its say.
  const [formError, setFormError] = useState<string | null>(null)
  const [hiddenArrival, setHiddenArrival] = useState<string | null>(null)
  const arrival = exchangeError ?? searchParams.get('error')
  const arrivalError = arrival === hiddenArrival ? null : arrival
  const error = formError ?? arrivalError

  const clearErrors = () => {
    setFormError(null)
    setHiddenArrival(arrival)
  }

  const sendCode = useMutation({
    mutationFn: () => client.post('/send-sign-in-code', { email }),
    onSuccess: () => {
      clearErrors()
      setView('verify-code')
    },
    onError: (err) => setFormError(refusalOf(err, 'Failed to send code')),
  })

  const show = (next: View) => {
    setView(next)
    clearErrors()
  }

  return (
    <Stack as="main" align="center" justify="center" className="login-page">
      <IgniLogo size={48} />

      <Heading as="h1" className="login-heading">
        {view === 'verify-code' ? 'Enter verification code' : 'Sign in to igni'}
      </Heading>

      <Stack className="login-card" gap="normal" padding="normal">
        {error && <Banner variant="critical" title={error} onDismiss={clearErrors} />}

        {view === 'verify-code' && (
          <SignInCodeVerifyForm
            email={email}
            resending={sendCode.isPending}
            onSignedIn={signedIn}
            onRefused={setFormError}
          />
        )}

        {view === 'password' && (
          <Stack gap="normal">
            <PasswordSignInForm
              email={email}
              onEmailChange={setEmail}
              onSignedIn={signedIn}
              onRefused={setFormError}
            />
            <SwitchMethod label="Sign in with email code instead" onClick={() => show('request-code')} />
            <OtherWaysIn methods={methods} returnTo={returnTo} />
          </Stack>
        )}

        {view === 'request-code' && (
          <Stack gap="normal">
            <SignInCodeRequestForm
              email={email}
              onEmailChange={setEmail}
              onRequest={() => sendCode.mutate()}
              sending={sendCode.isPending}
            />
            <SwitchMethod label="Sign in with password instead" onClick={() => show('password')} />
            <OtherWaysIn methods={methods} returnTo={returnTo} />
          </Stack>
        )}
      </Stack>

      <Stack className="login-footer" align="center" padding="normal">
        {view === 'verify-code' ? (
          <Text as="p" size="medium">
            Didn&apos;t receive a code?{' '}
            <Link as="button" onClick={() => sendCode.mutate()} muted={sendCode.isPending}>
              Resend
            </Link>
            {' · '}
            <Link as="button" onClick={() => show('request-code')}>
              Use a different email
            </Link>
          </Text>
        ) : (
          <Text as="p" size="medium">
            New to igni?{' '}
            <Link href="/signup" inline>
              Create an account
            </Link>
            {' · '}
            <Link href="/reset-password" inline>
              Forgot password?
            </Link>
          </Text>
        )}
      </Stack>
    </Stack>
  )
}

function SwitchMethod({ label, onClick }: { label: string; onClick: () => void }) {
  return (
    <Text as="p" size="small" className="login-hint" style={{ textAlign: 'center' }}>
      <Link as="button" onClick={onClick}>
        {label}
      </Link>
    </Text>
  )
}
