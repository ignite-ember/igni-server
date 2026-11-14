import { useState } from 'react'
import { Banner, Button, Checkbox, Dialog, FormControl, Select, Stack, TextInput } from '@primer/react'
import { useMutation, useQueryClient } from '@tanstack/react-query'

import client from '../../api/client'
import { refusalOf } from '../../api/refusal'
import type { BackendKind, NodeProbeResult } from '../../types'
import { authComplete, defaultAuth, KIND_LABEL, methodsOf, useAuthMethods } from './auth'
import { CloudBackendFields } from './CloudBackendFields'
import { INFERENCE_STATUS_KEY } from './fleet'
import {
  EMPTY_DRAFT,
  modelsIn,
  type NodeDraft,
  pinnedNodeOf,
  probeRequestOf,
  suggestedNodeId,
  toggledModels,
} from './nodeDraft'
import { ProbeOutcome } from './ProbeOutcome'

/**
 * Add a backend. A GPU node in two steps: say where it is, press Check,
 * and pick from the models actually there — a mistyped model id never
 * errors, it just never matches a request. A cloud backend says where it
 * is, what it serves, and how igni server signs in to it.
 */
export function NodeDialog({ onClose }: { onClose: () => void }) {
  const queryClient = useQueryClient()
  const [draft, setDraft] = useState<NodeDraft>(EMPTY_DRAFT)
  const [probe, setProbe] = useState<NodeProbeResult | null>(null)
  const [error, setError] = useState<string | null>(null)
  const edit = (change: Partial<NodeDraft>) => setDraft((current) => ({ ...current, ...change }))
  const table = useAuthMethods()
  const methods = methodsOf(table.data, draft.kind)
  const method = methods.find((m) => m.method === draft.auth.method)
  const cloud = draft.kind !== 'openai'

  function chooseKind(kind: BackendKind) {
    // A new kind starts from its own default method, keeping only the name.
    setDraft({
      ...EMPTY_DRAFT,
      kind,
      nodeId: draft.nodeId,
      auth: defaultAuth(methodsOf(table.data, kind)),
    })
    setProbe(null)
  }

  const cloudReady =
    modelsIn(draft.modelsText).length > 0 &&
    (draft.kind === 'vertex_openai'
      ? Boolean(draft.project.trim() && draft.region.trim())
      : Boolean(draft.baseUrl.trim())) &&
    authComplete(method, draft.auth)
  const ready = Boolean(draft.nodeId) && (cloud ? cloudReady : Boolean(probe) && draft.models.length > 0)

  const check = useMutation({
    mutationFn: () =>
      client.post<NodeProbeResult>('/portal/inference/nodes/discover', probeRequestOf(draft)).then((r) => r.data),
    onMutate: () => setError(null),
    onSuccess: (result) => {
      setProbe(result)
      setDraft((current) => ({
        ...current,
        models: result.models,
        nodeId: current.nodeId || (suggestedNodeId(current.baseUrl) ?? ''),
      }))
    },
    onError: () => setError('Could not reach that address.'),
  })

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
    <Dialog title="Add a backend" onClose={onClose} width="xlarge">
      <Stack gap="normal">
        {error && <Banner variant="critical" title="Could not add the node" description={error} />}

        <FormControl required>
          <FormControl.Label>Kind</FormControl.Label>
          <Select value={draft.kind} onChange={(e) => chooseKind(e.target.value as BackendKind)} block>
            {(table.data ?? [{ kind: 'openai' as BackendKind }]).map((row) => (
              <Select.Option key={row.kind} value={row.kind}>
                {KIND_LABEL[row.kind]}
              </Select.Option>
            ))}
          </Select>
        </FormControl>

        {cloud && <CloudBackendFields draft={draft} methods={methods} onChange={edit} />}

        {!cloud && (
          <>
            <FormControl required>
              <FormControl.Label>Address</FormControl.Label>
              <Stack direction="horizontal" gap="condensed" align="center">
                <TextInput
                  value={draft.baseUrl}
                  placeholder="http://gpu-rack-01.internal:8000/v1"
                  onChange={(e) => {
                    edit({ baseUrl: e.target.value })
                    setProbe(null)
                  }}
                  block
                  className="inference-url-field"
                />
                <Button
                  onClick={() => check.mutate()}
                  loading={check.isPending}
                  disabled={!draft.baseUrl || check.isPending}
                >
                  Check
                </Button>
              </Stack>
              <FormControl.Caption>
                The OpenAI-compatible endpoint. We ask it which models it serves.
              </FormControl.Caption>
            </FormControl>

            <FormControl>
              <FormControl.Label>Token</FormControl.Label>
              <TextInput
                type="password"
                value={draft.apiKey}
                onChange={(e) => edit({ apiKey: e.target.value })}
                block
              />
              <FormControl.Caption>
                Only if the node sits behind an auth proxy. Most private-network vLLM nodes have none.
              </FormControl.Caption>
            </FormControl>

            {probe && (
              <ProbeOutcome
                probe={probe}
                models={draft.models}
                onToggleModel={(name) => edit({ models: toggledModels(draft.models, name) })}
              />
            )}
          </>
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
            Roughly the vLLM instance&apos;s <code>--max-num-seqs</code>. Adapters share it — they share the GPU.
          </FormControl.Caption>
        </FormControl>

        <FormControl>
          <Checkbox checked={draft.multimodal} onChange={(e) => edit({ multimodal: e.target.checked })} />
          <FormControl.Label>Accepts images</FormControl.Label>
        </FormControl>

        <Stack direction="horizontal" gap="condensed" justify="end">
          <Button onClick={onClose}>Cancel</Button>
          <Button
            variant="primary"
            onClick={() => add.mutate()}
            loading={add.isPending}
            disabled={!ready || add.isPending}
          >
            Add node
          </Button>
        </Stack>
      </Stack>
    </Dialog>
  )
}
