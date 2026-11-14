import { useState } from 'react'
import { Banner, Button, Checkbox, Dialog, FormControl, Select, Stack, Text, TextInput } from '@primer/react'
import { useMutation, useQueryClient } from '@tanstack/react-query'

import client from '../../api/client'
import { refusalOf } from '../../api/refusal'
import type { BackendKind, NodeProbeResult } from '../../types'
import { defaultAuth, methodsOf, useAuthMethods } from './auth'
import { CloudBackendFields } from './CloudBackendFields'
import { INFERENCE_STATUS_KEY } from './fleet'
import { GpuNodeFields } from './GpuNodeFields'
import { isCloud, KINDS } from './kinds'
import { draftProblem, EMPTY_DRAFT, type NodeDraft, pinnedNodeOf } from './nodeDraft'

/**
 * Add a node: the customer's own GPU node, or a cloud's model service.
 * The kinds and their auth methods come from the server's own table; until
 * it has loaded only a GPU node can be added, and if it cannot load the
 * dialog says so rather than quietly offering less.
 */
export function NodeDialog({ onClose }: { onClose: () => void }) {
  const queryClient = useQueryClient()
  const table = useAuthMethods()
  const [draft, setDraft] = useState<NodeDraft>(EMPTY_DRAFT)
  const [probe, setProbe] = useState<NodeProbeResult | null>(null)
  const [error, setError] = useState<string | null>(null)
  const edit = (change: Partial<NodeDraft>) => setDraft((current) => ({ ...current, ...change }))
  const methods = methodsOf(table.data, draft.kind)
  const method = methods.find((m) => m.method === draft.auth.method)

  const kinds: BackendKind[] = table.data?.map((row) => row.kind) ?? ['openai']
  const problem = draftProblem(draft, method, { adding: true, probe })

  function chooseKind(value: string) {
    const kind = kinds.find((k) => k === value)
    if (!kind) return
    // A new kind starts from its own default method, keeping only the name.
    setDraft({ ...EMPTY_DRAFT, kind, nodeId: draft.nodeId, auth: defaultAuth(methodsOf(table.data, kind)) })
    setProbe(null)
  }

  const add = useMutation({
    mutationFn: () => client.post('/portal/inference/nodes', pinnedNodeOf(draft, method)).then((r) => r.data),
    onMutate: () => setError(null),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: INFERENCE_STATUS_KEY })
      onClose()
    },
    onError: (err) => setError(refusalOf(err, 'Could not add the node.')),
  })

  return (
    <Dialog title="Add a node" onClose={onClose} width="xlarge">
      <Stack gap="normal">
        {error && <Banner variant="critical" title="Could not add the node" description={error} />}
        {table.isError && (
          <Banner
            variant="warning"
            title="Cloud providers are unavailable"
            description="The server's list of providers and how they sign in did not load, so only a GPU node can be added. Reload to try again."
          />
        )}

        <FormControl required>
          <FormControl.Label>Kind</FormControl.Label>
          <Select value={draft.kind} onChange={(e) => chooseKind(e.target.value)} block>
            {kinds.map((kind) => (
              <Select.Option key={kind} value={kind}>
                {KINDS[kind].label}
              </Select.Option>
            ))}
          </Select>
          {table.isLoading && <FormControl.Caption>Loading the cloud providers…</FormControl.Caption>}
        </FormControl>

        {isCloud(draft.kind) ? (
          <CloudBackendFields draft={draft} methods={methods} onChange={edit} />
        ) : (
          <GpuNodeFields draft={draft} probe={probe} onChange={edit} onProbe={setProbe} onError={setError} />
        )}

        {/* Not behind the probe: a node that is not up yet can still be configured. */}
        <FormControl required>
          <FormControl.Label>Name</FormControl.Label>
          <TextInput value={draft.nodeId} onChange={(e) => edit({ nodeId: e.target.value })} block />
          <FormControl.Caption>
            Must be unique across every source. Used to track this node&apos;s load.
          </FormControl.Caption>
        </FormControl>

        <FormControl>
          <FormControl.Label>Concurrent requests</FormControl.Label>
          <TextInput
            type="number"
            value={String(draft.maxConcurrent)}
            onChange={(e) => edit({ maxConcurrent: Number(e.target.value) || 1 })}
          />
          <FormControl.Caption>
            Roughly the vLLM instance&apos;s <code>--max-num-seqs</code>; for a cloud, the most this deployment should
            send it at once.
          </FormControl.Caption>
        </FormControl>

        <FormControl>
          <Checkbox checked={draft.multimodal} onChange={(e) => edit({ multimodal: e.target.checked })} />
          <FormControl.Label>Accepts images</FormControl.Label>
        </FormControl>

        <Stack direction="horizontal" gap="condensed" justify="end" align="center">
          {problem && (
            <Text size="small" className="inference-muted">
              {problem}
            </Text>
          )}
          <Button onClick={onClose}>Cancel</Button>
          <Button
            variant="primary"
            onClick={() => add.mutate()}
            loading={add.isPending}
            disabled={problem !== null || add.isPending}
          >
            Add node
          </Button>
        </Stack>
      </Stack>
    </Dialog>
  )
}
