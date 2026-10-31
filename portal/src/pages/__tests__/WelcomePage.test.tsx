/**
 * WelcomePage — the post-signup landing page that shows install commands.
 * Pure presentational; no API calls.
 */
import { describe, expect, test } from 'vitest'
import userEvent from '@testing-library/user-event'
import { screen } from '@testing-library/react'
import { Routes, Route } from 'react-router-dom'
import { renderWithProviders } from '../../test/render'
import { WelcomePage } from '../WelcomePage'

describe('WelcomePage', () => {
  test('renders both install commands', () => {
    renderWithProviders(<WelcomePage />)

    // Homebrew and pip — the two supported install paths. These keep
    // the `ignite-ember` name because that is what the package is
    // *published* as: the PyPI project and the Homebrew formula. The
    // command it installs is `igni`.
    expect(
      screen.getByText('brew install ignite-ember/tap/ignite-ember'),
    ).toBeInTheDocument()
    expect(screen.getByText('pip install ignite-ember')).toBeInTheDocument()
  })

  test('the authenticate step is a command that runs', () => {
    // This assertion used to read
    // `getByText('ignite-ember /login')` — and it was green for as
    // long as the page said that, which is the problem: `/login` is a
    // slash command *inside* the session, so the line it pinned exits
    // with "Got unexpected extra argument (/login)". Verified by
    // running it.
    //
    // A test that asserts a string is on screen cannot tell whether
    // the string is a command that works. So this asserts the shape
    // instead: the copy-me block is the bare command, and the slash
    // command is described as something typed at the prompt.
    renderWithProviders(<WelcomePage />)

    // `getAll`: `igni` appears twice — once as the command block and
    // once inside the prose describing the binary.
    expect(screen.getAllByText('igni').length).toBeGreaterThan(0)
    expect(screen.getByText(/at the prompt/i)).toBeInTheDocument()

    const blocks = Array.from(document.querySelectorAll('code, pre')).map(
      (el) => el.textContent?.trim() ?? '',
    )
    const slashAsArgument = blocks.filter((b) => /^(igni|ignite-ember)\b.*\s\/login/.test(b))
    expect(slashAsArgument, 'a slash command presented as a shell argument').toEqual([])
  })

  test('"Continue to dashboard" navigates to /dashboard', async () => {
    function Landing() {
      return <div data-testid="landing">dashboard-landing</div>
    }

    renderWithProviders(
      <Routes>
        <Route path="/welcome" element={<WelcomePage />} />
        <Route path="/dashboard" element={<Landing />} />
      </Routes>,
      { route: '/welcome' },
    )

    await userEvent.click(screen.getByRole('button', { name: /Continue to dashboard/i }))
    expect(await screen.findByTestId('landing')).toBeInTheDocument()
  })
})
