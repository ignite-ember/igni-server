/**
 * The licence banner: silent on the free base, one sentence per module
 * that needs attention otherwise — a lapse is a warning, read-only is
 * critical.
 */
import { describe, expect, test } from 'vitest'
import { http, HttpResponse } from 'msw'
import { screen, waitFor } from '@testing-library/react'
import { renderWithProviders } from '../../../test/render'
import { server } from '../../../test/server'
import { LicenceBanner } from '../LicenceBanner'

const GRACE =
  'The licence for CodeIndex expired on 29 October 2027. CodeIndex keeps working until 27 January 2028 (in 80 days), then becomes read-only. Install a renewed licence file to continue.'
const READ_ONLY =
  'The licence for Artifacts expired on 29 October 2027. Artifacts is read-only: you can read and export everything, but not change anything or start new work. Install a renewed licence file to continue.'

describe('LicenceBanner', () => {
  test('says nothing when nothing needs doing', async () => {
    let asked = false
    server.use(
      http.get('/v1/portal/licence/notice', () => {
        asked = true
        return HttpResponse.json({ warnings: [] })
      }),
    )

    const { container } = renderWithProviders(<LicenceBanner />)

    await waitFor(() => expect(asked).toBe(true))
    expect(container.querySelector('.licence-banner')).toBeNull()
  })

  test('shows each module that needs attention, in the server’s words', async () => {
    server.use(
      http.get('/v1/portal/licence/notice', () =>
        HttpResponse.json({
          warnings: [
            { module: 'codeindex', state: 'grace', message: GRACE },
            { module: 'artifacts', state: 'read_only', message: READ_ONLY },
          ],
        }),
      ),
    )

    renderWithProviders(<LicenceBanner />)

    expect(await screen.findByText(GRACE)).toBeInTheDocument()
    expect(screen.getByText(READ_ONLY)).toBeInTheDocument()
  })
})
