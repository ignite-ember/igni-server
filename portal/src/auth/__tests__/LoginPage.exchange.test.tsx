import { beforeEach, describe, expect, it, vi } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { MemoryRouter } from 'react-router-dom'
import { StrictMode } from 'react'
import { ThemeProvider } from '@primer/react'
import { LoginPage } from '../LoginPage'
import client from '../../api/client'

const login = vi.fn()
const navigate = vi.fn()

vi.mock('../useAuth', () => ({ useAuth: () => ({ login, isAuthenticated: false }) }))
vi.mock('react-router-dom', async () => {
  const actual = await vi.importActual<typeof import('react-router-dom')>('react-router-dom')
  return { ...actual, useNavigate: () => navigate }
})

/**
 * F107. The sign-in callback hands over a one-time code, not a token.
 *
 * The test that matters is the StrictMode one. `main.tsx` renders under
 * `StrictMode`, so every effect runs twice in development — and the code
 * is single-use, so a naive implementation posts it twice, gets a correct
 * 400 on the second, and paints an error over a sign-in that worked.
 * That is not a hypothetical: it is what the first version of this did.
 */
function renderLogin({ search = '', strict = false }: { search?: string; strict?: boolean } = {}) {
  const tree = (
    <ThemeProvider>
      <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
        <MemoryRouter initialEntries={[`/login${search}`]}>
          <LoginPage />
        </MemoryRouter>
      </QueryClientProvider>
    </ThemeProvider>
  )
  return render(strict ? <StrictMode>{tree}</StrictMode> : tree)
}

describe('the sign-in code exchange', () => {
  // `spyOn` records across tests in a file, and the last test asserted
  // "no exchange happened" against calls the earlier ones had made.
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('exchanges the code and signs the user in', async () => {
    login.mockClear()
    vi.spyOn(client, 'get').mockResolvedValue({ data: { password: true, oauth_providers: [], sso: { enabled: false, label: 'SSO', start_url: '/v1/auth/oidc' } } } as never)
    const post = vi.spyOn(client, 'post').mockResolvedValue({ data: { access_token: 'the.real.token' } } as never)

    renderLogin({ search: '?code=code-for-the-happy-path' })

    await waitFor(() => expect(post).toHaveBeenCalledWith('/auth/exchange', { code: 'code-for-the-happy-path' }))
    await waitFor(() => expect(login).toHaveBeenCalledWith('the.real.token'))
  })

  it('posts the code exactly once under StrictMode', async () => {
    // The whole point. A second post would be refused by the server —
    // correctly — and the failure would land on a user who just signed
    // in successfully.
    login.mockClear()
    vi.spyOn(client, 'get').mockResolvedValue({ data: { password: true, oauth_providers: [], sso: { enabled: false, label: 'SSO', start_url: '/v1/auth/oidc' } } } as never)
    const post = vi.spyOn(client, 'post').mockResolvedValue({ data: { access_token: 'the.real.token' } } as never)

    renderLogin({ search: '?code=code-for-the-strict-test', strict: true })

    await waitFor(() => expect(login).toHaveBeenCalledWith('the.real.token'))

    const exchanges = post.mock.calls.filter(([url]) => url === '/auth/exchange')
    expect(exchanges).toHaveLength(1)
  })

  it('says to sign in again when the code has been used', async () => {
    login.mockClear()
    vi.spyOn(client, 'get').mockResolvedValue({ data: { password: true, oauth_providers: [], sso: { enabled: false, label: 'SSO', start_url: '/v1/auth/oidc' } } } as never)
    vi.spyOn(client, 'post').mockRejectedValue(new Error('400'))

    renderLogin({ search: '?code=already-used' })

    await waitFor(() => expect(screen.getByText(/already been used or has expired/i)).toBeTruthy())
    expect(login).not.toHaveBeenCalled()
  })

  it('does nothing without a code', async () => {
    login.mockClear()
    vi.spyOn(client, 'get').mockResolvedValue({ data: { password: true, oauth_providers: [], sso: { enabled: false, label: 'SSO', start_url: '/v1/auth/oidc' } } } as never)
    const post = vi.spyOn(client, 'post').mockResolvedValue({ data: {} } as never)

    renderLogin()

    await waitFor(() => expect(client.get).toHaveBeenCalled())
    expect(post.mock.calls.filter(([url]) => url === '/auth/exchange')).toHaveLength(0)
    expect(login).not.toHaveBeenCalled()
  })

  it('still shows a provider error passed in the query', async () => {
    // The other thing that arrives on this URL. Read during render
    // rather than copied into state — F104.
    vi.spyOn(client, 'get').mockResolvedValue({ data: { password: true, oauth_providers: [], sso: { enabled: false, label: 'SSO', start_url: '/v1/auth/oidc' } } } as never)

    renderLogin({ search: '?error=OAuth%20sign-in%20failed' })

    await waitFor(() => expect(screen.getByText(/OAuth sign-in failed/i)).toBeTruthy())
  })
})
