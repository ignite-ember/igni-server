/**
 * A group's model: offered only what a pin can route to.
 */
import { describe, expect, test, vi } from 'vitest'
import { http, HttpResponse } from 'msw'
import userEvent from '@testing-library/user-event'
import { screen } from '@testing-library/react'
import { renderWithProviders } from '../../../test/render'
import { server } from '../../../test/server'
import { GroupModel } from '../GroupModel'

describe('GroupModel', () => {
  test('offers only models served on chat completions, not a /v1/messages-only one', async () => {
    server.use(
      http.get('/v1/portal/inference/models', () =>
        HttpResponse.json({
          models: ['MiniMax-M3.1-Flash', 'igni-mock-base', 'igni-mock-base-lora-sql'],
          chat_completions: ['igni-mock-base', 'igni-mock-base-lora-sql'],
        }),
      ),
    )

    renderWithProviders(<GroupModel groupId="g-1" current={null} isAdmin onFeedback={vi.fn()} />)
    await userEvent.click(await screen.findByRole('button', { name: 'Deployment default' }))

    expect(await screen.findByRole('menuitemradio', { name: 'igni-mock-base-lora-sql' })).toBeInTheDocument()
    expect(screen.queryByRole('menuitemradio', { name: 'MiniMax-M3.1-Flash' })).not.toBeInTheDocument()
  })
})
