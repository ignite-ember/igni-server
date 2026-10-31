import { describe, expect, it, vi } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { MemoryRouter } from 'react-router-dom'
import { ThemeProvider } from '@primer/react'
import { LoginPage } from '../LoginPage'
import client from '../../api/client'

vi.mock('../useAuth', () => ({ useAuth: () => ({ login: vi.fn(), isAuthenticated: false }) }))

function renderLogin() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <ThemeProvider>
      <QueryClientProvider client={queryClient}>
        <MemoryRouter initialEntries={['/login']}>
          <LoginPage />
        </MemoryRouter>
      </QueryClientProvider>
    </ThemeProvider>,
  )
}

/**
 * The page renders from `/auth/methods` rather than from hardcoded
 * buttons. Each test here corresponds to a deployment somebody actually
 * runs.
 */
describe('LoginPage sign-in methods', () => {
  it('shows only the providers the deployment enables', async () => {
    vi.spyOn(client, 'get').mockResolvedValue({
      data: { password: true, oauth_providers: ['google'], sso: { enabled: false, label: 'SSO', start_url: '/v1/auth/oidc' } },
    } as never)

    renderLogin()

    await waitFor(() => expect(screen.getByRole('button', { name: 'Google' })).toBeTruthy())
    // SSO the server did not offer. A button for it used to produce an
    // error page for a button that should not have existed.
    expect(screen.queryByRole('button', { name: 'SSO' })).toBeNull()
  })

  it('shows the SSO button under the name the operator chose', async () => {
    vi.spyOn(client, 'get').mockResolvedValue({
      data: { password: true, oauth_providers: [], sso: { enabled: true, label: 'Acme Okta', start_url: '/v1/auth/oidc' } },
    } as never)

    renderLogin()

    await waitFor(() => expect(screen.getByRole('button', { name: 'Acme Okta' })).toBeTruthy())
  })

  it('shows no divider when there is nothing to continue with', async () => {
    // An air-gapped deployment with local accounts only. "Or continue
    // with" above an empty space is the flaw this guards.
    vi.spyOn(client, 'get').mockResolvedValue({
      data: { password: true, oauth_providers: [], sso: { enabled: false, label: 'SSO', start_url: '/v1/auth/oidc' } },
    } as never)

    renderLogin()

    // Waited on the query settling rather than on any particular text.
    // Two earlier attempts — /password/i, then a "sign in" button — each
    // matched several elements, which is a fact about this page's copy
    // and not about what is being asserted.
    await waitFor(() => expect(client.get).toHaveBeenCalledWith('/auth/methods'))

    expect(screen.queryByText('Or continue with')).toBeNull()
    expect(screen.queryByRole('button', { name: 'Google' })).toBeNull()
  })

  it('renders nothing extra while the answer is still in flight', () => {
    vi.spyOn(client, 'get').mockReturnValue(new Promise(() => {}) as never)

    renderLogin()

    // A button that appears and then vanishes is worse than one that
    // appears a moment late.
    expect(screen.queryByText('Or continue with')).toBeNull()
  })

  it('ignores a provider name it has no button for', async () => {
    // A server that names a provider this portal has no button for must
    // not crash it, nor grow a button for a sign-in the portal cannot
    // drive. GitHub and GitLab sign-in left with the provider integration.
    vi.spyOn(client, 'get').mockResolvedValue({
      data: { password: true, oauth_providers: ['google', 'github', 'bitbucket'], sso: { enabled: false, label: 'SSO', start_url: '/v1/auth/oidc' } },
    } as never)

    renderLogin()

    await waitFor(() => expect(screen.getByRole('button', { name: 'Google' })).toBeTruthy())
    expect(screen.queryByRole('button', { name: /github/i })).toBeNull()
    expect(screen.queryByRole('button', { name: /bitbucket/i })).toBeNull()
  })
})
