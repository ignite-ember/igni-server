import { useState, type ReactNode } from 'react'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { ConfirmationDialog } from '@primer/react'
import client from '../api/client'
import type { RegisteredClient } from '../types'

export type ServiceDecision = 'approve' | 'revoke' | 'remove'

/**
 * A super admin's decision on a registered service, from the list or
 * from its own page.
 *
 * Approving happens at once; revoking and removing ask first. Both pages
 * show `decide.error` the same way — a "Not changed" banner with the
 * server's reason, which is how a 409 for an extension id another
 * approved service already ships reaches the person.
 */
export function useServiceDecision({ onRemoved }: { onRemoved?: () => void } = {}): {
  decide: ReturnType<typeof useDecideMutation>
  request: (registration: RegisteredClient, decision: ServiceDecision) => void
  dialog: ReactNode
} {
  const decide = useDecideMutation(onRemoved)
  const [confirming, setConfirming] = useState<{
    registration: RegisteredClient
    decision: 'revoke' | 'remove'
  } | null>(null)

  function request(registration: RegisteredClient, decision: ServiceDecision) {
    if (decision === 'approve') decide.mutate({ registration, decision })
    else setConfirming({ registration, decision })
  }

  const dialog = confirming && (
    <ConfirmationDialog
      title={
        confirming.decision === 'revoke'
          ? `Revoke ${confirming.registration.name}?`
          : `Remove ${confirming.registration.name}?`
      }
      confirmButtonContent={confirming.decision === 'revoke' ? 'Revoke' : 'Remove'}
      cancelButtonContent="Cancel"
      confirmButtonType="danger"
      onClose={(gesture) => {
        const chosen = confirming
        setConfirming(null)
        if (gesture === 'confirm') decide.mutate(chosen)
      }}
    >
      {confirming.decision === 'revoke'
        ? "It can no longer get tokens, and people's machines remove its extension the next time they look for updates. Tokens it already holds stop working within ten minutes."
        : 'The registration is forgotten. If the service is still running, it registers again the next time it starts and waits here for approval.'}
    </ConfirmationDialog>
  )

  return { decide, request, dialog }
}

function useDecideMutation(onRemoved?: () => void) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: ({ registration, decision }: { registration: RegisteredClient; decision: ServiceDecision }) => {
      // Each path written out whole, so the route check can match it.
      const id = encodeURIComponent(registration.client_id)
      if (decision === 'remove') return client.delete(`/portal/clients/${id}`)
      if (decision === 'approve') return client.post<RegisteredClient>(`/portal/clients/${id}/approve`)
      return client.post<RegisteredClient>(`/portal/clients/${id}/revoke`)
    },
    onSuccess: (_, { registration, decision }) => {
      void queryClient.invalidateQueries({ queryKey: ['registered-clients'] })
      if (decision === 'remove') {
        queryClient.removeQueries({ queryKey: ['registered-client', registration.client_id] })
        onRemoved?.()
      } else {
        void queryClient.invalidateQueries({ queryKey: ['registered-client', registration.client_id] })
      }
    },
  })
}
