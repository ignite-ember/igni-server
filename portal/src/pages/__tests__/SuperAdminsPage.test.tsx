/**
 * Super admins: who owns the install, making one by address, and the
 * server's refusal to remove the last one shown as it is.
 */
import { describe, expect, test } from 'vitest'
import { http, HttpResponse } from 'msw'
import userEvent from '@testing-library/user-event'
import { screen } from '@testing-library/react'
import { renderWithProviders } from '../../test/render'
import { server } from '../../test/server'
import { SuperAdminsPage } from '../SuperAdminsPage'

const ME = {
  user_id: 'boss',
  email: 'boss@acmecorp.io',
  full_name: 'The Boss',
  created_at: '2026-01-01T00:00:00Z',
  organizations: [],
  is_super_admin: true,
}

function serve(admins: { user_id: string; email: string; full_name: string | null }[]) {
  server.use(
    http.get('/v1/portal/me', () => HttpResponse.json(ME)),
    http.get('/v1/portal/super-admins', () => HttpResponse.json(admins)),
  )
}

describe('SuperAdminsPage', () => {
  test('lists the super admins', async () => {
    serve([{ user_id: 'boss', email: 'boss@acmecorp.io', full_name: 'The Boss' }])

    renderWithProviders(<SuperAdminsPage />)

    expect(await screen.findByText('The Boss')).toBeInTheDocument()
    expect(screen.getByText('boss@acmecorp.io')).toBeInTheDocument()
  })

  test('makes somebody a super admin by address', async () => {
    serve([{ user_id: 'boss', email: 'boss@acmecorp.io', full_name: 'The Boss' }])
    let sent: unknown = null
    server.use(
      http.post('/v1/portal/super-admins', async ({ request }) => {
        sent = await request.json()
        return HttpResponse.json({ user_id: 'deputy', email: 'deputy@acmecorp.io', full_name: null })
      }),
    )

    renderWithProviders(<SuperAdminsPage />)
    await screen.findByText('The Boss')
    await userEvent.type(screen.getByRole('textbox'), 'deputy@acmecorp.io')
    await userEvent.click(screen.getByRole('button', { name: 'Add' }))

    await expect.poll(() => sent).toEqual({ email: 'deputy@acmecorp.io' })
  })

  test("shows the server's reason when the last one cannot be removed", async () => {
    serve([{ user_id: 'boss', email: 'boss@acmecorp.io', full_name: 'The Boss' }])
    server.use(
      http.delete('/v1/portal/super-admins/:userId', () =>
        HttpResponse.json(
          { detail: 'This is the only super admin. Make somebody else one first.' },
          { status: 409 },
        ),
      ),
    )

    renderWithProviders(<SuperAdminsPage />)
    await userEvent.click(await screen.findByRole('button', { name: /Remove boss@acmecorp.io as super admin/i }))

    expect(await screen.findByText('This is the only super admin. Make somebody else one first.')).toBeInTheDocument()
  })

  test('is not for anybody else', async () => {
    server.use(http.get('/v1/portal/me', () => HttpResponse.json({ ...ME, is_super_admin: false })))

    renderWithProviders(<SuperAdminsPage />)

    expect(await screen.findByText('Super admins only')).toBeInTheDocument()
  })
})
