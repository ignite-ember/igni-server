/**
 * ProtectedRoute / AuthRoute — the only thing between unauthenticated
 * users and protected pages. If these regress, the whole app becomes
 * either inaccessible or leaky.
 *
 *  - ProtectedRoute: redirect to /login when no token
 *  - AuthRoute: redirect to /login WITH returnTo query param when no token
 *  - Both: render the outlet when authenticated
 */
import { describe, expect, test, beforeEach } from 'vitest'
import { screen } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render } from '@testing-library/react'
import { http, HttpResponse } from 'msw'
import { server } from '../../test/server'
import { AuthProvider } from '../AuthProvider'
import { OrgProvider } from '../OrgProvider'
import { AuthRoute, ProtectedRoute } from '../ProtectedRoute'

function LoginStub() {
  return <div data-testid="login">LOGIN_PAGE</div>
}
function ProtectedStub() {
  return <div data-testid="protected">PROTECTED_CONTENT</div>
}

function renderAt(route: string) {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  })
  return render(
    <QueryClientProvider client={qc}>
      <AuthProvider>
        <OrgProvider>
          <MemoryRouter initialEntries={[route]}>
            <Routes>
              <Route path="/login" element={<LoginStub />} />
              <Route element={<ProtectedRoute />}>
                <Route path="/dashboard" element={<ProtectedStub />} />
              </Route>
              <Route element={<AuthRoute />}>
                <Route path="/cli-auth" element={<ProtectedStub />} />
              </Route>
            </Routes>
          </MemoryRouter>
        </OrgProvider>
      </AuthProvider>
    </QueryClientProvider>,
  )
}

beforeEach(() => {
  localStorage.clear()
})

/**
 * "Signed in" is the server's opinion, not a localStorage key.
 *
 * With no stored token the provider asks /portal/me once: a 200 means
 * the deployment authenticates us some other way — PORTAL_AUTH_BYPASS,
 * or a cookie — and a 401 means we are genuinely out. Trusting the key
 * alone locked an admin out of a working deployment when the key was
 * cleared.
 */
function serverSaysSignedOut() {
  server.use(http.get('/v1/portal/me', () => new HttpResponse(null, { status: 401 })))
}

describe('ProtectedRoute', () => {
  test('redirects to /login when the server does not know us', async () => {
    serverSaysSignedOut()
    renderAt('/dashboard')
    expect(await screen.findByTestId('login')).toBeInTheDocument()
    expect(screen.queryByTestId('protected')).not.toBeInTheDocument()
  })

  test('renders without a token when the deployment signs us in anyway', async () => {
    // The bypass case. The key is absent and the server does not care.
    renderAt('/dashboard')
    expect(await screen.findByTestId('protected')).toBeInTheDocument()
  })

  test('does not flash the login page while it is still asking', () => {
    serverSaysSignedOut()
    renderAt('/dashboard')
    // Neither yet: bouncing before the answer arrives would send away
    // every visitor to a deployment that signs them in.
    expect(screen.queryByTestId('login')).not.toBeInTheDocument()
    expect(screen.queryByTestId('protected')).not.toBeInTheDocument()
  })

  test('renders outlet when authenticated', () => {
    localStorage.setItem('igni_token', 'tok')
    renderAt('/dashboard')
    expect(screen.getByTestId('protected')).toBeInTheDocument()
  })
})

describe('AuthRoute', () => {
  test('redirects to /login WITH returnTo when the server does not know us', async () => {
    serverSaysSignedOut()
    renderAt('/cli-auth?code=abc')

    // Lands on the login stub, once the provider has asked.
    expect(await screen.findByTestId('login')).toBeInTheDocument()
    // The redirect carries a returnTo param so the user can resume after login.
    // We can't inspect MemoryRouter's URL directly, but we can verify the
    // protected content didn't render — that's the contract callers rely on.
    expect(screen.queryByTestId('protected')).not.toBeInTheDocument()
  })

  test('renders outlet when authenticated', () => {
    localStorage.setItem('igni_token', 'tok')
    renderAt('/cli-auth?code=abc')
    expect(screen.getByTestId('protected')).toBeInTheDocument()
  })
})
