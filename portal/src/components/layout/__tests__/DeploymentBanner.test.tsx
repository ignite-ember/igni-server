/**
 * The deployment banner: the startup checks' warnings, shown to super
 * admins only — and not even asked for on anybody else's behalf, since
 * the server would refuse. F35: sign-in codes in the log was a warning
 * only whoever reads the log ever saw.
 */
import { describe, expect, test } from 'vitest'
import { http, HttpResponse } from 'msw'
import { screen, waitFor } from '@testing-library/react'
import { renderWithProviders } from '../../../test/render'
import { server } from '../../../test/server'
import { DeploymentBanner } from '../DeploymentBanner'

const CODES_IN_THE_LOG =
  'LOG_SIGN_IN_CODES is on and no mail relay is configured, so every sign-in, sign-up and password-reset code is written to the application log. Anyone who can read these logs can sign in as any user and reset any password — treat them as credential material, and turn LOG_SIGN_IN_CODES off once a relay is configured.'

describe('DeploymentBanner', () => {
  test('shows a super admin each notice, in the server’s words', async () => {
    server.use(
      http.get('/v1/portal/deployment/notices', () => HttpResponse.json({ notices: [CODES_IN_THE_LOG] })),
    )

    renderWithProviders(<DeploymentBanner isSuperAdmin />)

    expect(await screen.findByText(CODES_IN_THE_LOG)).toBeInTheDocument()
  })

  test('says nothing when there is nothing to say', async () => {
    let asked = false
    server.use(
      http.get('/v1/portal/deployment/notices', () => {
        asked = true
        return HttpResponse.json({ notices: [] })
      }),
    )

    const { container } = renderWithProviders(<DeploymentBanner isSuperAdmin />)

    await waitFor(() => expect(asked).toBe(true))
    expect(container.querySelector('.deployment-banner')).toBeNull()
  })

  test('is never asked for by anybody else', async () => {
    let asked = false
    server.use(
      http.get('/v1/portal/deployment/notices', () => {
        asked = true
        return HttpResponse.json({ notices: [CODES_IN_THE_LOG] })
      }),
    )

    const { container } = renderWithProviders(<DeploymentBanner isSuperAdmin={false} />)

    // Give a request the chance to go out, then check that none did.
    await new Promise((resolve) => setTimeout(resolve, 50))
    expect(asked).toBe(false)
    expect(container.querySelector('.deployment-banner')).toBeNull()
  })
})
