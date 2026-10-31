import { describe, expect, it, vi } from 'vitest'
import { act, render, screen, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { createMemoryRouter, MemoryRouter, Route, RouterProvider, Routes } from 'react-router-dom'
import { ThemeProvider } from '@primer/react'
import { CLIAuthPage } from '../CLIAuthPage'
import client from '../../api/client'

vi.mock('../useAuth', () => ({ useAuth: () => ({ login: vi.fn(), isAuthenticated: true }) }))

/**
 * The conditional-hooks crash, as a regression test.
 *
 * `if (!port) return …` used to sit above this component's `useRef` and
 * `useCallback`, so the number of hooks React saw depended on a query
 * parameter. Going from `?port=51234` to no port re-renders the same
 * component instance with two fewer hooks, and React throws "Rendered
 * fewer hooks than expected. This may be caused by an accidental early
 * return statement."
 *
 * eslint's `react-hooks/rules-of-hooks` had been reporting it for as long
 * as it has existed in this file; nothing ran eslint in CI.
 */
function renderAt(search: string) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <ThemeProvider>
      <QueryClientProvider client={queryClient}>
        <MemoryRouter initialEntries={[`/cli-auth${search}`]}>
          <Routes>
            <Route path="/cli-auth" element={<CLIAuthPage />} />
          </Routes>
        </MemoryRouter>
      </QueryClientProvider>
    </ThemeProvider>,
  )
}

describe('CLIAuthPage', () => {
  it('explains itself when the port is missing', async () => {
    vi.spyOn(client, 'get').mockResolvedValue({ data: { organizations: [] } } as never)

    renderAt('')

    await waitFor(() => expect(screen.getByText('Invalid Request')).toBeTruthy())
  })

  it('survives losing the port on a same-route navigation', async () => {
    // The crash. One router, one component instance, two renders — with
    // the parameter and then without. `rerender` with a fresh
    // MemoryRouter was the first attempt and proved nothing: a new
    // router remounts the tree, which is the one case that was never
    // broken.
    vi.spyOn(client, 'get').mockResolvedValue({ data: { organizations: [] } } as never)

    const router = createMemoryRouter(
      [{ path: '/cli-auth', element: <CLIAuthPage /> }],
      { initialEntries: ['/cli-auth?port=51234'] },
    )

    render(
      <ThemeProvider>
        <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
          <RouterProvider router={router} />
        </QueryClientProvider>
      </ThemeProvider>,
    )

    await act(async () => {
      await router.navigate('/cli-auth')
    })

    await waitFor(() => expect(screen.getByText('Invalid Request')).toBeTruthy())
  })

  // A third test lived here and has been removed. It asserted on the
  // shape of the source — that every conditional return appears after
  // the last hook call — and when the fix was reverted to check that the
  // tests bite, it passed: the regex expected `return (` and the
  // sabotage wrote `return <div>`. eslint's `react-hooks/rules-of-hooks`
  // checks the same property properly, and now runs in CI, so a weaker
  // second copy of that rule is worse than none.
})
