import { useEffect, useState } from 'react'
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
import { useMutation, useQuery } from '@tanstack/react-query'
import { ShieldLockIcon } from '@primer/octicons-react'
import client from '../api/client'
import { useAuth } from './useAuth'
import { IgniLogo } from '../components/IgniLogo'
import { safeReturnTo } from './returnTo'
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

/**
 * Sign-in codes this page load has already sent to `/auth/exchange`.
 *
 * Module scope, and that is the load-bearing detail. `main.tsx` renders
 * under `StrictMode`, so React mounts, unmounts and remounts on the
 * first render — which means component state and `useRef` are both
 * recreated, and an effect guarded by either posts the code twice. The
 * code is single-use, so the second post gets a correct 400 and paints
 * an error over a sign-in that worked.
 *
 * Two earlier attempts, both wrong: a `cancelled` flag in the effect
 * cleanup suppresses the *first* run's success and lets the second one
 * fail, which is precisely backwards; and a `useRef` does not survive
 * the remount. Measured — the StrictMode test counts the posts.
 *
 * Unbounded in principle and one entry per sign-in per page load in
 * practice, which is not worth a cache.
 */
const EXCHANGED_CODES = new Set<string>()

type OAuthProvider = 'google'

const PROVIDER_BUTTONS: Record<OAuthProvider, { label: string; icon: typeof GoogleIcon }> = {
  google: { label: 'Google', icon: GoogleIcon },
}

interface AuthMethods {
  password: boolean
  oauth_providers: string[]
  sso: { enabled: boolean; label: string; start_url: string }
}

/**
 * The sign-in methods this deployment actually offers.
 *
 * Was two hardcoded copies of three buttons, one per branch of the form.
 * Both problems that fixed are worth naming:
 *
 * - a deployment that narrows `OAUTH_PROVIDERS` still showed all three,
 *   so somebody clicked a provider the server refuses and got an error
 *   page for a button that should not have been there;
 * - OIDC had no button at all. The endpoints existed and nothing linked
 *   to them, which for the only provider an air-gapped deployment can
 *   reach means the feature was unreachable by design.
 *
 * The divider is inside this component on purpose: with no providers and
 * no SSO, "Or continue with" above nothing is worse than showing
 * nothing.
 */
function OtherWaysIn({
  methods,
  onOAuth,
  onSso,
}: {
  methods: AuthMethods | undefined
  onOAuth: (provider: OAuthProvider) => void
  onSso: () => void
}) {
  // Nothing at all until the answer arrives. A button that appears and
  // then vanishes is worse than one that appears a moment late, and this
  // request is served from the same origin as the page.
  if (!methods) return null

  const providers = methods.oauth_providers.filter(
    (name): name is OAuthProvider => name in PROVIDER_BUTTONS,
  )
  if (providers.length === 0 && !methods.sso.enabled) return null

  return (
    <>
      <Stack direction="horizontal" align="center" gap="normal" className="login-divider-row">
        <span className="login-divider-line" />
        <Text size="small" className="login-divider-text">Or continue with</Text>
        <span className="login-divider-line" />
      </Stack>

      <Stack gap="condensed">
        {methods.sso.enabled && (
          <Button block variant="primary" leadingVisual={ShieldLockIcon} onClick={onSso}>
            {methods.sso.label}
          </Button>
        )}

        {providers.map((name) => {
          const button = PROVIDER_BUTTONS[name]
          return (
            <Button key={name} block leadingVisual={button.icon} onClick={() => onOAuth(name)}>
              {button.label}
            </Button>
          )
        })}
      </Stack>
    </>
  )
}

type SignInMethod = 'code' | 'password'
type Step = 'email' | 'code'

export function LoginPage() {
  useDocumentTitle('Sign In', 'Sign in to igni with email, password, Google or single sign-on.')
  const navigate = useNavigate()
  const [searchParams] = useSearchParams()
  const { login } = useAuth()

  const [step, setStep] = useState<Step>('email')
  const [method, setMethod] = useState<SignInMethod>('password')
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [code, setCode] = useState('')
  // Two kinds of error with one banner. What the forms produce is state;
  // what an OAuth or SSO redirect hands back is in the URL, and copying
  // it into state with an effect is the cascading render React warns
  // about. So the URL one is read during render and the two are merged.
  const [formError, setFormError] = useState<string | null>(null)
  const error = formError ?? searchParams.get('error')

  // Where to go after login — /cli-auth?port=... for CLI flow, /dashboard
  // for the portal. Filtered: this value survives a round trip through
  // an identity provider and is then handed to `navigate()`, so
  // `?returnTo=//evil.example.com` would be an open redirect off the
  // back of a real sign-in. See returnTo.ts for what the router already
  // refuses and why this does not rely on that.
  const returnTo = safeReturnTo(searchParams.get('returnTo'))

  // Which methods this deployment offers. Without this the page showed
  // hardcoded providers whether or not the server accepted them,
  // and never showed SSO at all.
  const { data: methods } = useQuery<AuthMethods>({
    queryKey: ['auth-methods'],
    queryFn: async () => (await client.get<AuthMethods>('/auth/methods')).data,
    // It changes when somebody edits the deployment's configuration and
    // restarts it, so there is no reason to ask again during a session.
    staleTime: Infinity,
  })

  // A one-time code in the query string means a provider just sent the
  // browser back here. It is exchanged for the token over POST rather
  // than the token arriving in the URL — see
  // `app/db/redis/auth_handoff.py` for what that URL used to leak into.
  //
  // Storing the token and navigating are both changes to things outside
  // React, which is what an effect is for. The error branch used to live
  // here too and does not need to: it is read during render above.
  useEffect(() => {
    const code = searchParams.get('code')
    if (!code || EXCHANGED_CODES.has(code)) return
    EXCHANGED_CODES.add(code)

    client
      .post<{ access_token: string }>('/auth/exchange', { code })
      .then((response) => {
        login(response.data.access_token)
        navigate(returnTo, { replace: true })
      })
      .catch(() => {
        // Single-use and thirty seconds long, so the realistic causes
        // are a reloaded callback page or a bookmarked one. Both mean
        // "sign in again", which is what this says.
        setFormError('That sign-in link has already been used or has expired. Please sign in again.')
      })
  }, [searchParams, login, navigate, returnTo])

  // Password sign-in
  const passwordMutation = useMutation({
    mutationFn: () =>
      client.post<{ access_token: string }>('/sign-in', { email, password }),
    onSuccess: (response) => {
      login(response.data.access_token)
      navigate(returnTo, { replace: true })
    },
    onError: (err: unknown) => {
      const message =
        (err as { response?: { data?: { detail?: string } } })?.response?.data
          ?.detail || 'Invalid email or password'
      setFormError(message)
    },
  })

  // OTP code sign-in
  const sendCodeMutation = useMutation({
    mutationFn: () => client.post('/send-sign-in-code', { email }),
    onSuccess: () => {
      setFormError(null)
      setStep('code')
    },
    onError: (err: unknown) => {
      const message =
        (err as { response?: { data?: { detail?: string } } })?.response?.data
          ?.detail || 'Failed to send code'
      setFormError(message)
    },
  })

  const verifyCodeMutation = useMutation({
    mutationFn: () =>
      client.post<{ access_token: string }>('/sign-in-with-code', {
        email,
        code,
      }),
    onSuccess: (response) => {
      login(response.data.access_token)
      navigate(returnTo, { replace: true })
    },
    onError: (err: unknown) => {
      const message =
        (err as { response?: { data?: { detail?: string } } })?.response?.data
          ?.detail || 'Invalid code'
      setFormError(message)
    },
  })

  const handlePasswordSignIn = (e: React.FormEvent) => {
    e.preventDefault()
    if (!email.trim() || !password.trim()) return
    passwordMutation.mutate()
  }

  const handleSendCode = (e: React.FormEvent) => {
    e.preventDefault()
    if (!email.trim()) return
    sendCodeMutation.mutate()
  }

  const handleVerifyCode = (e: React.FormEvent) => {
    e.preventDefault()
    if (!code.trim()) return
    verifyCodeMutation.mutate()
  }

  const isLoading =
    passwordMutation.isPending ||
    sendCodeMutation.isPending ||
    verifyCodeMutation.isPending

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
    // The start URL comes from the server rather than being built here,
    // so the one place that knows where the flow begins is the one that
    // implements it.
    window.location.href = `${methods?.sso.start_url ?? '/v1/auth/oidc'}${params}`
  }

  const switchMethod = (newMethod: SignInMethod) => {
    setMethod(newMethod)
    setFormError(null)
    setPassword('')
    setCode('')
    setStep('email')
  }

  const heading =
    step === 'code'
      ? 'Enter verification code'
      : 'Sign in to igni'

  return (
    <Stack as="main" align="center" justify="center" className="login-page">
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
            onDismiss={() => setFormError(null)}
          />
        )}

        {step === 'code' ? (
          /* ── OTP code verification ── */
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
                disabled={isLoading || !code.trim()}
              >
                {isLoading ? <Spinner size="small" /> : 'Verify'}
              </Button>
            </Stack>
          </form>
        ) : method === 'password' ? (
          /* ── Password sign-in ── */
          <Stack gap="normal">
            <form onSubmit={handlePasswordSignIn}>
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

                <Button
                  type="submit"
                  variant="primary"
                  block
                  disabled={isLoading || !email.trim() || !password.trim()}
                >
                  {isLoading ? <Spinner size="small" /> : 'Sign in'}
                </Button>
              </Stack>
            </form>

            <Text as="p" size="small" className="login-hint" style={{ textAlign: 'center' }}>
              <Link as="button" onClick={() => switchMethod('code')}>
                Sign in with email code instead
              </Link>
            </Text>

            <OtherWaysIn methods={methods} onOAuth={handleOAuth} onSso={handleSso} />
          </Stack>
        ) : (
          /* ── OTP code sign-in (email step) ── */
          <Stack gap="normal">
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
                  {isLoading ? <Spinner size="small" /> : 'Send sign-in code'}
                </Button>
              </Stack>
            </form>

            <Text as="p" size="small" className="login-hint" style={{ textAlign: 'center' }}>
              <Link as="button" onClick={() => switchMethod('password')}>
                Sign in with password instead
              </Link>
            </Text>

            <OtherWaysIn methods={methods} onOAuth={handleOAuth} onSso={handleSso} />
          </Stack>
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
                setFormError(null)
              }}
            >
              Use a different email
            </Link>
          </Text>
        ) : (
          <Text as="p" size="medium">
            New to igni?{' '}
            <Link href="/signup" inline>
              Create an account
            </Link>
            {' \u00b7 '}
            <Link href="/reset-password" inline>
              Forgot password?
            </Link>
          </Text>
        )}
      </Stack>
    </Stack>
  )
}
