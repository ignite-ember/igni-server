/**
 * nodeDraft — the add and edit forms' one rule set.
 *
 * Both forms say why a draft cannot be sent before the API would, in the same
 * words, and nothing an admin typed is dropped on the way to the request.
 */
import { describe, expect, test } from 'vitest'

import type { AuthMethodInfo, NodeProbeResult } from '../../../types'
import { draftProblem, EMPTY_DRAFT, type NodeDraft, parseModelIds, pinnedNodeOf } from '../nodeDraft'

const AMBIENT: AuthMethodInfo = { method: 'ambient', settings: [], secret: null }
const PROBED: NodeProbeResult = {
  reachable: true,
  models: ['qwen'],
  detail: null,
  latency_ms: 12,
  needs_credentials: false,
}

const AZURE: NodeDraft = {
  ...EMPTY_DRAFT,
  kind: 'azure_openai',
  nodeId: 'azure-eu',
  baseUrl: 'https://acme.openai.azure.com/openai/v1',
  modelsText: 'gpt-5',
  auth: { method: 'ambient', settings: {}, secret: '' },
}

describe('parseModelIds', () => {
  test('reads each name = provider-name line, and keeps every other non-blank line as rejected', () => {
    expect(parseModelIds('gpt-5 = acme-gpt5\n\n  claude=eu.anthropic.claude-v1:0  \ngpt-4: acme-gpt4\n= x')).toEqual({
      pairs: { 'gpt-5': 'acme-gpt5', claude: 'eu.anthropic.claude-v1:0' },
      rejected: ['gpt-4: acme-gpt4', '= x'],
    })
  })
})

describe('draftProblem', () => {
  test('a complete cloud backend has none, adding or editing', () => {
    expect(draftProblem(AZURE, AMBIENT, { adding: true })).toBeNull()
    expect(draftProblem(AZURE, AMBIENT, { adding: false })).toBeNull()
  })

  test.each([
    [{ baseUrl: 'acme.openai.azure.com' }, 'The address has to start with http:// or https://'],
    [{ modelsText: ' , ' }, 'A node needs at least one model, or nothing will ever be routed to it.'],
    [
      { modelIdsText: 'gpt-5 = acme-gpt5\ngpt-5: typo' },
      'Each provider name is a line "name = provider-name", and this is not: gpt-5: typo',
    ],
    [{ maxConcurrent: 0 }, 'Concurrency has to be at least 1.'],
    [{ nodeId: '  ' }, 'Give the node a name.'],
  ])('%o is refused, saying why', (change, sentence) => {
    expect(draftProblem({ ...AZURE, ...change }, AMBIENT, { adding: true })).toBe(sentence)
  })

  test('an edit keeps its name, so a blank one is not asked for', () => {
    expect(draftProblem({ ...AZURE, nodeId: '' }, AMBIENT, { adding: false })).toBeNull()
  })

  test('a cloud method whose table has not loaded cannot be sent', () => {
    expect(draftProblem(AZURE, undefined, { adding: true })).toBe(
      'The chosen authentication needs every field filled in.',
    )
  })

  test('a stored secret counts as given on edit, and not on add', () => {
    const key: AuthMethodInfo = { method: 'api_key', settings: [], secret: 'api_key' }
    const draft: NodeDraft = { ...AZURE, auth: { method: 'api_key', settings: {}, secret: '' } }
    expect(draftProblem(draft, key, { adding: false, secretStored: true })).toBeNull()
    expect(draftProblem(draft, key, { adding: true })).toBe('The chosen authentication needs every field filled in.')
  })

  test('adding a GPU node needs it checked and a model picked; editing one needs its address and models typed', () => {
    const gpu: NodeDraft = { ...EMPTY_DRAFT, nodeId: 'gpu-1', baseUrl: 'http://gpu-1:8000/v1' }
    expect(draftProblem(gpu, undefined, { adding: true, probe: null })).toBe(
      'Check the node first, to see which models it serves.',
    )
    expect(draftProblem(gpu, undefined, { adding: true, probe: PROBED })).toBe('Pick at least one model to route here.')
    expect(draftProblem({ ...gpu, models: ['qwen'] }, undefined, { adding: true, probe: PROBED })).toBeNull()
    expect(draftProblem(gpu, undefined, { adding: false })).toBe(
      'A node needs at least one model, or nothing will ever be routed to it.',
    )
    expect(draftProblem({ ...gpu, modelsText: 'qwen' }, undefined, { adding: false })).toBeNull()
  })
})

describe('pinnedNodeOf', () => {
  test('sends the provider names that parsed', () => {
    expect(pinnedNodeOf({ ...AZURE, modelIdsText: 'gpt-5 = acme-gpt5' }, AMBIENT).model_ids).toEqual({
      'gpt-5': 'acme-gpt5',
    })
  })
})
