import { useMutation, useQueryClient } from '@tanstack/react-query'
import { Button, Stack, Text } from '@primer/react'

import client from '../../api/client'
import { refusalOf } from '../../api/refusal'
import type { OnFeedback } from './feedback'

/** Issuing (or re-issuing) the credential the app's cloud VM works with. */
export function AppCloudCredential({ appId, onFeedback }: { appId: string; onFeedback: OnFeedback }) {
  const queryClient = useQueryClient()
  const enableCloud = useMutation({
    mutationFn: () => client.post(`/portal/apps/${appId}/vm-credential`),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['vm-credentials'] })
      onFeedback('success', "Issued. The node agent hands it to the app's VM when it creates it; nobody sees it.")
    },
    onError: (error) => onFeedback('critical', refusalOf(error)),
  })

  return (
    <Stack gap="condensed">
      <Text weight="semibold">Cloud</Text>
      <Text size="small">
        The app&apos;s VM holds a credential of its own, picked up once by the node agent. Issuing again stops the old
        one at once.
      </Text>
      <Stack direction="horizontal">
        <Button onClick={() => enableCloud.mutate()} disabled={enableCloud.isPending}>
          Enable cloud / re-issue VM credential
        </Button>
      </Stack>
    </Stack>
  )
}
