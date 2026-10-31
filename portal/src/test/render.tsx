/**
 * `renderWithProviders` — drop a component into RTL with router + react-query
 * + Primer + auth/org providers wired up so most pages just work.
 */
import type { ReactElement } from 'react'
import { render, type RenderOptions, type RenderResult } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { BaseStyles, ThemeProvider } from '@primer/react'
import { MemoryRouter } from 'react-router-dom'
import { AuthProvider } from '../auth/AuthProvider'
import { OrgProvider } from '../auth/OrgProvider'

interface ProvidersOptions {
  /** Initial URL the MemoryRouter starts at. */
  route?: string
  /** Custom QueryClient — defaults to a fresh one with no retries. */
  queryClient?: QueryClient
}

export function renderWithProviders(
  ui: ReactElement,
  { route = '/', queryClient, ...rtlOptions }: ProvidersOptions & RenderOptions = {},
): RenderResult & { queryClient: QueryClient } {
  const client =
    queryClient ??
    new QueryClient({
      defaultOptions: {
        queries: { retry: false, staleTime: Infinity },
        mutations: { retry: false },
      },
    })

  const wrapper = ({ children }: { children: React.ReactNode }) => (
    <QueryClientProvider client={client}>
      <ThemeProvider colorMode="auto">
        <BaseStyles>
          <AuthProvider>
            <OrgProvider>
              <MemoryRouter initialEntries={[route]}>{children}</MemoryRouter>
            </OrgProvider>
          </AuthProvider>
        </BaseStyles>
      </ThemeProvider>
    </QueryClientProvider>
  )

  const result = render(ui, { wrapper, ...rtlOptions })
  return { ...result, queryClient: client }
}
