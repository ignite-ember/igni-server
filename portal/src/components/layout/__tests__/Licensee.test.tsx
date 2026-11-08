/**
 * "Licensed to <customer> · <licence id>" for everybody signed in, and
 * nothing at all without a verified licence — which is what the visual
 * baselines, recorded with no licence, picture.
 */
import { describe, expect, test } from 'vitest'
import { http, HttpResponse } from 'msw'
import { screen, waitFor } from '@testing-library/react'
import { renderWithProviders } from '../../../test/render'
import { server } from '../../../test/server'
import { Licensee } from '../Licensee'

describe('Licensee', () => {
  test('names the licensee and the licence', async () => {
    server.use(
      http.get('/v1/portal/licence/notice', () =>
        HttpResponse.json({ warnings: [], customer: 'Example Bank N.V.', licence_id: 'IE-2026-0042' }),
      ),
    )

    const { container } = renderWithProviders(<Licensee />)

    await waitFor(() => expect(container.querySelector('.sidebar-licensee')).not.toBeNull())
    expect(container.querySelector('.sidebar-licensee')).toHaveTextContent(
      'Licensed to Example Bank N.V. · IE-2026-0042',
    )
    expect(screen.getByText('IE-2026-0042')).toBeInTheDocument()
  })

  test('renders nothing with no licence installed', async () => {
    let asked = false
    server.use(
      http.get('/v1/portal/licence/notice', () => {
        asked = true
        return HttpResponse.json({ warnings: [], customer: null, licence_id: null })
      }),
    )

    const { container } = renderWithProviders(<Licensee />)

    await waitFor(() => expect(asked).toBe(true))
    expect(container.querySelector('.sidebar-licensee')).toBeNull()
    expect(screen.queryByText(/Licensed to/)).toBeNull()
  })

  test('renders nothing for a server that does not name one', async () => {
    // The notice before it carried the licensee, and the visual fixtures recorded from one.
    let asked = false
    server.use(
      http.get('/v1/portal/licence/notice', () => {
        asked = true
        return HttpResponse.json({ warnings: [] })
      }),
    )

    const { container } = renderWithProviders(<Licensee />)

    await waitFor(() => expect(asked).toBe(true))
    expect(container.querySelector('.sidebar-licensee')).toBeNull()
    expect(screen.queryByText(/Licensed to/)).toBeNull()
  })
})
