/**
 * The licence page: whose licence it is and where each module stands,
 * installing one from a file or a paste, and the server's two refusals —
 * not a licence this release accepts (422), and valid but a mounted
 * Secret (409) — shown so an admin knows what to do next.
 */
import { describe, expect, test } from 'vitest'
import { http, HttpResponse } from 'msw'
import userEvent from '@testing-library/user-event'
import { screen, waitFor, within } from '@testing-library/react'
import { renderWithProviders } from '../../test/render'
import { server } from '../../test/server'
import { NO_LICENCE } from '../../test/handlers'
import { LicencePage } from '../LicencePage'

const ME = {
  user_id: 'boss',
  email: 'boss@acmecorp.io',
  full_name: 'The Boss',
  created_at: '2026-01-01T00:00:00Z',
  organizations: [],
  is_super_admin: true,
}

const EXPIRES = '2027-10-29T00:00:00Z'
const GRACE =
  'The licence for Artifacts expired on 29 October 2027. Artifacts keeps working until 27 January 2028 (in 80 days), then becomes read-only. Install a renewed licence file to continue.'

const LICENSED = {
  ...NO_LICENCE,
  installed: true,
  problem: null,
  licence_id: 'IE-2026-0042',
  customer: 'Example Bank N.V.',
  kind: 'commercial',
  issued: '2026-10-29T00:00:00Z',
  modules: NO_LICENCE.modules.map((status) => {
    const licensed = { ...status, licence_id: 'IE-2026-0042', customer: 'Example Bank N.V.', kind: 'commercial' }
    if (status.module === 'codeindex')
      return { ...licensed, state: 'valid', reason: null, users: 100, expires: EXPIRES, days_left: 356, message: null }
    if (status.module === 'artifacts')
      return { ...licensed, state: 'grace', reason: 'lapsed', users: 50, expires: EXPIRES, message: GRACE }
    return {
      ...licensed,
      reason: 'not_licensed',
      message: `${status.message?.split(' is not')[0]} is not licensed on this deployment. The installed licence does not include it.`,
    }
  }),
}

/** A licence file as Ignite Ember sends it. The page only passes it on; the server verifies it. */
const ENVELOPE = { igni_licence: 1, key_id: 'ie-2026', payload: 'eyJmb3JtYXQiOjF9', signature: 'c2lnbmF0dXJl' }

function serve(overview: object = LICENSED) {
  server.use(
    http.get('/v1/portal/me', () => HttpResponse.json(ME)),
    http.get('/v1/portal/licence', () => HttpResponse.json(overview)),
  )
}

function day(value: string) {
  return new Date(value).toLocaleDateString(undefined, { dateStyle: 'medium', timeZone: 'UTC' })
}

describe('LicencePage', () => {
  test('says whose licence it is, and where each module stands', async () => {
    serve()

    renderWithProviders(<LicencePage />)

    const licence = await screen.findByLabelText('Installed licence')
    expect(within(licence).getByText('Example Bank N.V.')).toBeInTheDocument()
    expect(within(licence).getByText('IE-2026-0042')).toBeInTheDocument()
    expect(within(licence).getByText('Commercial')).toBeInTheDocument()
    expect(within(licence).getByText(day('2026-10-29T00:00:00Z'))).toBeInTheDocument()

    const table = screen.getByRole('table', { name: 'Paid modules' })
    const rows = within(table).getAllByRole('row').slice(1)
    expect(rows).toHaveLength(5)
    const codeindex = within(table).getByRole('rowheader', { name: 'CodeIndex' }).closest('tr')!
    expect(within(codeindex).getByText('Valid')).toBeInTheDocument()
    expect(within(codeindex).getByText(day(EXPIRES))).toBeInTheDocument()
    expect(within(codeindex).getByText('100')).toBeInTheDocument()
    const artifacts = within(table).getByRole('rowheader', { name: 'Artifacts' }).closest('tr')!
    expect(within(artifacts).getByText('Grace period')).toBeInTheDocument()
    expect(within(artifacts).getByText(GRACE)).toBeInTheDocument()
    const fleet = within(table).getByRole('rowheader', { name: 'Fleet' }).closest('tr')!
    expect(within(fleet).getByText('Not licensed')).toBeInTheDocument()
  })

  test('says so when no licence is installed', async () => {
    serve(NO_LICENCE)

    renderWithProviders(<LicencePage />)

    expect(await screen.findByText(/No licence is installed\. The free base needs none/)).toBeInTheDocument()
    expect(screen.queryByLabelText('Installed licence')).toBeNull()
  })

  test('says so when the installed file does not verify', async () => {
    serve({
      ...NO_LICENCE,
      installed: true,
      problem: 'invalid_signature',
      modules: NO_LICENCE.modules.map((status) => ({
        ...status,
        reason: 'invalid_signature',
        message: 'CodeIndex is not licensed on this deployment. The installed licence has been altered and is not valid.',
      })),
    })

    renderWithProviders(<LicencePage />)

    expect(await screen.findByText('The installed file is not a valid licence')).toBeInTheDocument()
    expect(screen.getByText(/has been altered and is not valid\. Install the file exactly/)).toBeInTheDocument()
  })

  test('installs a pasted licence, as it was pasted', async () => {
    serve(NO_LICENCE)
    let sent: unknown = null
    server.use(
      http.put('/v1/portal/licence', async ({ request }) => {
        sent = await request.json()
        return HttpResponse.json(LICENSED)
      }),
    )

    renderWithProviders(<LicencePage />)
    await screen.findByText(/The free base needs none/)
    await userEvent.click(screen.getByRole('textbox', { name: 'Or paste its contents' }))
    await userEvent.paste(JSON.stringify(ENVELOPE, null, 2))
    await userEvent.click(screen.getByRole('button', { name: 'Install licence' }))

    expect(await screen.findByText('Licence IE-2026-0042 for Example Bank N.V. is installed.')).toBeInTheDocument()
    expect(sent).toEqual(ENVELOPE)
    // The page shows the new licence without asking again.
    expect(within(screen.getByLabelText('Installed licence')).getByText('Example Bank N.V.')).toBeInTheDocument()
  })

  test('installs a chosen file', async () => {
    serve(NO_LICENCE)
    let sent: unknown = null
    server.use(
      http.put('/v1/portal/licence', async ({ request }) => {
        sent = await request.json()
        return HttpResponse.json(LICENSED)
      }),
    )

    renderWithProviders(<LicencePage />)
    await screen.findByText(/The free base needs none/)
    const file = new File([JSON.stringify(ENVELOPE)], 'example-bank.licence.json', { type: 'application/json' })
    await userEvent.upload(screen.getByLabelText('Licence file'), file)
    // What is about to be sent is on the screen.
    await waitFor(() =>
      expect(screen.getByRole('textbox', { name: 'Or paste its contents' })).toHaveValue(JSON.stringify(ENVELOPE)),
    )
    await userEvent.click(screen.getByRole('button', { name: 'Install licence' }))

    await expect.poll(() => sent).toEqual(ENVELOPE)
  })

  test('shows why a file was refused, and what to do about it', async () => {
    serve(NO_LICENCE)
    server.use(
      http.put('/v1/portal/licence', () =>
        HttpResponse.json(
          {
            detail: {
              reason: 'invalid_signature',
              message: 'This file is not a licence igni accepts (invalid_signature).',
            },
          },
          { status: 422 },
        ),
      ),
    )

    renderWithProviders(<LicencePage />)
    await screen.findByText(/The free base needs none/)
    await userEvent.click(screen.getByRole('textbox', { name: 'Or paste its contents' }))
    await userEvent.paste(JSON.stringify(ENVELOPE))
    await userEvent.click(screen.getByRole('button', { name: 'Install licence' }))

    expect(
      await screen.findByText(
        /This file is not a licence igni accepts \(invalid_signature\)\. It has been changed since Ignite Ember signed it/,
      ),
    ).toBeInTheDocument()
  })

  test('says where to install it when the licence is a mounted Secret', async () => {
    serve(NO_LICENCE)
    const message =
      'The licence is valid, but /etc/igni/licence/licence.json cannot be written from here. Install it where the licence is mounted (the licence Secret in Kubernetes) instead.'
    server.use(
      http.put('/v1/portal/licence', () =>
        HttpResponse.json({ detail: { reason: 'licence_path_read_only', message } }, { status: 409 }),
      ),
    )

    renderWithProviders(<LicencePage />)
    await screen.findByText(/The free base needs none/)
    await userEvent.click(screen.getByRole('textbox', { name: 'Or paste its contents' }))
    await userEvent.paste(JSON.stringify(ENVELOPE))
    await userEvent.click(screen.getByRole('button', { name: 'Install licence' }))

    expect(await screen.findByText(message)).toBeInTheDocument()
    expect(screen.getByText('The licence is valid, but was not installed')).toBeInTheDocument()
  })

  test('sends nothing that is not JSON', async () => {
    serve(NO_LICENCE)
    let asked = false
    server.use(
      http.put('/v1/portal/licence', () => {
        asked = true
        return HttpResponse.json(LICENSED)
      }),
    )

    renderWithProviders(<LicencePage />)
    await screen.findByText(/The free base needs none/)
    await userEvent.click(screen.getByRole('textbox', { name: 'Or paste its contents' }))
    await userEvent.paste('"igni_licence": 1, "key_id"')
    await userEvent.click(screen.getByRole('button', { name: 'Install licence' }))

    expect(await screen.findByText(/That is not JSON/)).toBeInTheDocument()
    expect(asked).toBe(false)
  })

  test('is not for anybody else', async () => {
    server.use(http.get('/v1/portal/me', () => HttpResponse.json({ ...ME, is_super_admin: false })))

    renderWithProviders(<LicencePage />)

    expect(await screen.findByText('Super admins only')).toBeInTheDocument()
  })
})
