import { describe, expect, test } from 'vitest'

import { attentionItems, commandFailed } from '../attention'
import {
  API_KEYS,
  AUDIT,
  COMMANDS,
  ERASURES,
  INFERENCE,
  NOW,
  TEAM,
  VM_CREDENTIALS,
  VM_PROFILES,
} from './fixtures'

const ids = (items: ReturnType<typeof attentionItems>) => items.map((i) => i.id)

describe('what needs attention', () => {
  test('nothing read, nothing said', () => {
    expect(attentionItems({}, NOW)).toEqual([])
  })

  test('each area says its one thing, the urgent ones first', () => {
    const items = attentionItems(
      {
        team: TEAM,
        inference: INFERENCE,
        vmProfiles: VM_PROFILES,
        erasures: ERASURES,
        audit: AUDIT,
        apiKeys: API_KEYS,
        vmCredentials: VM_CREDENTIALS,
        commands: COMMANDS,
      },
      NOW,
    )
    expect(ids(items)).toEqual([
      'inference-unwell',
      'commands-failed',
      'vm-profiles-over-caps',
      'team-invited',
      'api-keys-expiring',
      'vm-credentials-awaiting',
      'erasures-soon',
      'audit-denied',
    ])
    expect(items.slice(0, 3).every((i) => i.tone === 'danger')).toBe(true)
    expect(items.find((i) => i.id === 'inference-unwell')?.detail).toBe('gpu-b')
    expect(items.find((i) => i.id === 'team-invited')?.title).toBe('1 invitation not accepted yet')
  })

  test('no node serving is said once, instead of every node being unwell', () => {
    const drained = {
      ...INFERENCE,
      pools: INFERENCE.pools.map((p) => ({
        ...p,
        nodes: p.nodes.map((n) => ({ ...n, drain: { by: 'u1', by_email: null, at: '2026-06-01T00:00:00Z', reason: null } })),
      })),
    }
    expect(ids(attentionItems({ inference: drained }, NOW))).toEqual(['inference-none'])
    expect(ids(attentionItems({ inference: { ...INFERENCE, pools: [] } }, NOW))).toEqual(['inference-none'])
  })

  test('a node in two pools is counted once', () => {
    const shared = { ...INFERENCE, pools: [...INFERENCE.pools, { ...INFERENCE.pools[0], pool: 'codeindex' as const }] }
    expect(attentionItems({ inference: shared }, NOW)[0].title).toBe('1 inference node not healthy')
  })

  test('an expired key and a key about to expire are told apart', () => {
    const keys = [...API_KEYS, { ...API_KEYS[1], key_id: 'k3', name: 'old', expired: true }]
    expect(ids(attentionItems({ apiKeys: keys }, NOW))).toEqual(['api-keys-expiring', 'api-keys-expired'])
  })

  test('a held erasure is not also said to be soon', () => {
    const held = { ...ERASURES, erasures: [{ ...ERASURES.erasures[0], legal_hold: true }] }
    expect(ids(attentionItems({ erasures: held }, NOW))).toEqual(['erasures-held'])
  })

  test('a command failed when it finished with an error or a non-zero exit', () => {
    expect(COMMANDS.map(commandFailed)).toEqual([false, true, false])
    expect(commandFailed({ ...COMMANDS[0], error: 'timed out', exit_code: null })).toBe(true)
  })
})
