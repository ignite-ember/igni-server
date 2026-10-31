/**
 * /authorize — signing a person in to a registered client.
 *
 * What matters most is what does *not* happen: a refused request never
 * sends the browser anywhere. And the request survives a sign-in: a
 * signed-out person goes through /login and comes back to the same
 * request, query and all.
 */
import { beforeEach, describe, expect, test, vi } from 'vitest'
import { http, HttpResponse } from 'msw'
import userEvent from '@testing-library/user-event'
import { screen, waitFor } from '@testing-library/react'
import { Route, Routes } from 'react-router-dom'
import { renderWithProviders } from '../../test/render'
import { server } from '../../test/server'
import { AuthRoute } from '../ProtectedRoute'
import { LoginPage } from '../LoginPage'
import { AuthorizePage } from '../AuthorizePage'

// `window.location.assign` is unforgeable on jsdom's Location, so it
// cannot be spied on in place. Replace `location` with a stub that
// keeps the origin — MSW resolves relative URLs against it — and
// records where the page tried to send the browser.
const assign = vi.fn()
Object.defineProperty(window, 'location', {
  configurable: true,
  writable: true,
  value: {
    href: 'http://localhost/',
    origin: 'http://localhost',
    protocol: 'http:',
    host: 'localhost',
    hostname: 'localhost',
    port: '',
    pathname: '/',
    search: '',
    hash: '',
    assign,
    replace: () => {},
    reload: () => {},
  },
})

const CHALLENGE = 'E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM'
const REDIRECT_URI = 'https://codeindex.acmecorp.io/auth/callback'

function authorizeUrl(overrides: Record<string, string> = {}) {
  const query = new URLSearchParams({
    response_type: 'code',
    client_id: 'client-codeindex',
    redirect_uri: REDIRECT_URI,
    state: 'af0ifjsldkj',
    code_challenge: CHALLENGE,
    code_challenge_method: 'S256',
    ...overrides,
  })
  return `/authorize?${query.toString()}`
}

function renderAuthorize(route = authorizeUrl()) {
  return renderWithProviders(
    <Routes>
      <Route path="/login" element={<LoginPage />} />
      <Route element={<AuthRoute />}>
        <Route path="/authorize" element={<AuthorizePage />} />
      </Route>
    </Routes>,
    { route },
  )
}

function serveAsk(organisations: { org_id: string; name: string }[]) {
  const asked: { client_id: string | null; redirect_uri: string | null }[] = []
  server.use(
    http.get('/v1/portal/oauth/request', ({ request }) => {
      const url = new URL(request.url)
      asked.push({ client_id: url.searchParams.get('client_id'), redirect_uri: url.searchParams.get('redirect_uri') })
      return HttpResponse.json({ client_name: 'CodeIndex', organisations })
    }),
  )
  return asked
}

function serveAuthorize() {
  const sent: unknown[] = []
  server.use(
    http.post('/v1/portal/oauth/authorize', async ({ request }) => {
      sent.push(await request.json())
      return HttpResponse.json({ redirect_to: `${REDIRECT_URI}?code=one-time-code&state=af0ifjsldkj` })
    }),
  )
  return sent
}

beforeEach(() => {
  localStorage.clear()
  assign.mockClear()
})

describe('AuthorizePage', () => {
  test('one organisation: continue sends the browser to the code redirect', async () => {
    localStorage.setItem('igni_token', 'tok')
    const asked = serveAsk([{ org_id: 'org-acme', name: 'Acme' }])
    const sent = serveAuthorize()

    renderAuthorize()

    expect(await screen.findByRole('heading', { name: 'Sign in to CodeIndex' })).toBeInTheDocument()
    expect(asked).toEqual([{ client_id: 'client-codeindex', redirect_uri: REDIRECT_URI }])
    // Chosen for them: there is nothing to choose between.
    expect(screen.getByText('Acme')).toBeInTheDocument()

    await userEvent.click(screen.getByRole('button', { name: 'Continue' }))

    await waitFor(() => expect(assign).toHaveBeenCalledWith(`${REDIRECT_URI}?code=one-time-code&state=af0ifjsldkj`))
    expect(sent).toEqual([
      {
        client_id: 'client-codeindex',
        redirect_uri: REDIRECT_URI,
        organisation: 'org-acme',
        code_challenge: CHALLENGE,
        code_challenge_method: 'S256',
        state: 'af0ifjsldkj',
      },
    ])
  })

  test('several organisations: the person picks one before continuing', async () => {
    localStorage.setItem('igni_token', 'tok')
    serveAsk([
      { org_id: 'org-acme', name: 'Acme' },
      { org_id: 'org-globex', name: 'Globex' },
    ])
    const sent = serveAuthorize()

    renderAuthorize()

    const proceed = await screen.findByRole('button', { name: 'Continue' })
    expect(proceed).toBeDisabled()
    await userEvent.click(screen.getByRole('radio', { name: 'Globex' }))
    await userEvent.click(proceed)

    await waitFor(() => expect(assign).toHaveBeenCalled())
    expect(sent).toEqual([expect.objectContaining({ organisation: 'org-globex' })])
  })

  test('in no organisation: says so and offers nothing to continue', async () => {
    localStorage.setItem('igni_token', 'tok')
    serveAsk([])

    renderAuthorize()

    expect(await screen.findByText(/not in any organisation/i)).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Continue' })).not.toBeInTheDocument()
  })

  test('a refused request is shown and goes nowhere', async () => {
    localStorage.setItem('igni_token', 'tok')
    let posted = false
    server.use(
      http.get('/v1/portal/oauth/request', () =>
        HttpResponse.json({ detail: 'This application is not approved here' }, { status: 404 }),
      ),
      http.post('/v1/portal/oauth/authorize', () => {
        posted = true
        return HttpResponse.json({ redirect_to: REDIRECT_URI })
      }),
    )

    renderAuthorize()

    expect(await screen.findByText('This application is not approved here')).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: 'This sign-in cannot continue' })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Continue' })).not.toBeInTheDocument()
    expect(posted).toBe(false)
    expect(assign).not.toHaveBeenCalled()
  })

  test('a refusal of the final step is shown and goes nowhere', async () => {
    localStorage.setItem('igni_token', 'tok')
    serveAsk([{ org_id: 'org-acme', name: 'Acme' }])
    server.use(
      http.post('/v1/portal/oauth/authorize', () =>
        HttpResponse.json({ detail: 'Not a member of this organisation' }, { status: 403 }),
      ),
    )

    renderAuthorize()
    await userEvent.click(await screen.findByRole('button', { name: 'Continue' }))

    expect(await screen.findByText('Not a member of this organisation')).toBeInTheDocument()
    expect(assign).not.toHaveBeenCalled()
  })

  test.each([
    ['a response type other than code', { response_type: 'token' }, /Only response_type=code/],
    ['a plain code challenge', { code_challenge_method: 'plain' }, /no S256 code challenge/],
    ['no code challenge', { code_challenge: '' }, /no S256 code challenge/],
    ['no redirect address', { redirect_uri: '' }, /where to return to/],
  ])('refuses %s without asking the server', async (_name, overrides, reason) => {
    localStorage.setItem('igni_token', 'tok')
    const asked = serveAsk([{ org_id: 'org-acme', name: 'Acme' }])

    renderAuthorize(authorizeUrl(overrides))

    expect(await screen.findByText(reason)).toBeInTheDocument()
    expect(asked).toEqual([])
    expect(assign).not.toHaveBeenCalled()
  })
})

describe('signing in on the way', () => {
  test('a signed-out person signs in and comes back to the same request', async () => {
    // Signed out until a token is presented — the server's opinion, as
    // AuthProvider asks it, rather than a localStorage key.
    server.use(
      http.get('/v1/portal/me', ({ request }) =>
        request.headers.get('Authorization') === 'Bearer tok-1'
          ? HttpResponse.json({ user_id: 'u-1', email: 'a@acmecorp.io', full_name: null, created_at: null })
          : new HttpResponse(null, { status: 401 }),
      ),
      http.get('/v1/auth/methods', () =>
        HttpResponse.json({
          password: true,
          oauth_providers: [],
          sso: { enabled: false, label: 'SSO', start_url: '/v1/auth/oidc' },
        }),
      ),
      http.post('/v1/sign-in', () => HttpResponse.json({ access_token: 'tok-1' })),
    )
    const asked = serveAsk([{ org_id: 'org-acme', name: 'Acme' }])
    const sent = serveAuthorize()

    renderAuthorize()

    // Sent to sign in first; nothing about the request asked yet.
    await userEvent.type(await screen.findByLabelText(/Email address/i), 'a@acmecorp.io')
    await userEvent.type(screen.getByLabelText(/Password/i), 'pw')
    expect(asked).toEqual([])
    await userEvent.click(screen.getByRole('button', { name: /^Sign in$/i }))

    // Back on the request, with every part of the original query.
    expect(await screen.findByRole('heading', { name: 'Sign in to CodeIndex' })).toBeInTheDocument()
    expect(asked).toEqual([{ client_id: 'client-codeindex', redirect_uri: REDIRECT_URI }])

    await userEvent.click(screen.getByRole('button', { name: 'Continue' }))
    await waitFor(() => expect(assign).toHaveBeenCalledWith(`${REDIRECT_URI}?code=one-time-code&state=af0ifjsldkj`))
    expect(sent).toEqual([
      expect.objectContaining({ code_challenge: CHALLENGE, code_challenge_method: 'S256', state: 'af0ifjsldkj' }),
    ])
  })
})
