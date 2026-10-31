import type { RegisteredClientState } from '../types'

/** Waiting for a decision first, then what is in use, then what was stopped. */
export const STATE_ORDER: Record<RegisteredClientState, number> = { pending: 0, approved: 1, revoked: 2 }

export const STATE_LABEL: Record<
  RegisteredClientState,
  { text: string; variant: 'attention' | 'success' | 'danger' }
> = {
  pending: { text: 'Waiting for approval', variant: 'attention' },
  approved: { text: 'Approved', variant: 'success' },
  revoked: { text: 'Revoked', variant: 'danger' },
}
