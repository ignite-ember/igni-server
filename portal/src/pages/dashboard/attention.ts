import type {
  ApiKey,
  AuditEventPage,
  ErasureList,
  InferenceStatus,
  NodeStatus,
  TeamMemberList,
  VmCommandRecord,
  VmCredential,
  VmProfilesOverview,
} from '../../types'
import { countOf } from '../../utils/plural'
import { daysUntil } from './format'
import type { AttentionItem } from './types'

/** An API key this close to expiring is worth a word. */
export const KEY_EXPIRY_WARNING_DAYS = 7
/** An erasure this close to running is worth a word. */
export const ERASURE_WARNING_DAYS = 7

/** What the dashboard has read; anything it was not allowed or has not got yet is undefined. */
export interface AttentionInputs {
  team?: TeamMemberList
  inference?: InferenceStatus
  vmProfiles?: VmProfilesOverview
  erasures?: ErasureList
  audit?: AuditEventPage
  apiKeys?: ApiKey[]
  vmCredentials?: VmCredential[]
  commands?: VmCommandRecord[]
}

/** Every node once, though it may serve several pools. */
export function distinctNodes(inference: InferenceStatus): NodeStatus[] {
  const byId = new Map<string, NodeStatus>()
  for (const pool of inference.pools) for (const node of pool.nodes) byId.set(node.id, node)
  return [...byId.values()]
}

/** A command that ran and did not succeed. */
export function commandFailed(command: VmCommandRecord): boolean {
  return command.state === 'finished' && (command.error !== null || (command.exit_code ?? 0) !== 0)
}

/**
 * What needs someone's eye, most urgent first.
 *
 * Each rule reads one area and says so in a sentence, with where to go;
 * an area that was not read (not allowed, still loading) says nothing.
 */
export function attentionItems(inputs: AttentionInputs, now: Date = new Date()): AttentionItem[] {
  const items: AttentionItem[] = []

  if (inputs.inference) {
    const nodes = distinctNodes(inputs.inference)
    const serving = nodes.filter((n) => !n.drain)
    const unwell = serving.filter((n) => n.health.state !== 'healthy')
    if (serving.length === 0) {
      items.push({
        id: 'inference-none',
        tone: 'danger',
        title: 'No inference node is serving',
        detail: nodes.length
          ? `All ${countOf(nodes.length, 'node')} are drained, so nobody's requests are answered.`
          : 'Nothing has registered yet, so nobody\'s requests are answered.',
        to: '/inference',
      })
    } else if (unwell.length) {
      items.push({
        id: 'inference-unwell',
        tone: 'danger',
        title: `${countOf(unwell.length, 'inference node')} not healthy`,
        detail: unwell.map((n) => n.id).join(', '),
        to: '/inference',
      })
    }
  }

  const failed = inputs.commands?.filter(commandFailed) ?? []
  if (failed.length) {
    items.push({
      id: 'commands-failed',
      tone: 'danger',
      title: `${countOf(failed.length, 'recent command')} failed in a VM`,
      detail: failed[0].command,
      to: '/vm-commands',
    })
  }

  const overCaps = inputs.vmProfiles?.profiles.filter((p) => !p.within_caps) ?? []
  if (overCaps.length) {
    items.push({
      id: 'vm-profiles-over-caps',
      tone: 'danger',
      title: `${countOf(overCaps.length, 'VM profile')} over the platform's caps`,
      detail: overCaps.map((p) => p.name).join(', '),
      to: '/vm-profiles',
    })
  }

  const invited = inputs.team?.members.filter((m) => m.status === 'invited') ?? []
  if (invited.length) {
    items.push({
      id: 'team-invited',
      tone: 'attention',
      title: `${countOf(invited.length, 'invitation')} not accepted yet`,
      detail: invited.map((m) => m.email).join(', '),
      to: '/team',
    })
  }

  const keys = inputs.apiKeys ?? []
  const expired = keys.filter((k) => k.expired)
  const expiring = keys.filter(
    (k) => !k.expired && daysUntil(k.expires_at, now) <= KEY_EXPIRY_WARNING_DAYS,
  )
  if (expiring.length) {
    items.push({
      id: 'api-keys-expiring',
      tone: 'attention',
      title: `${countOf(expiring.length, 'API key')} expiring within ${KEY_EXPIRY_WARNING_DAYS} days`,
      detail: expiring.map((k) => k.name).join(', '),
      to: '/api-keys',
    })
  }
  if (expired.length) {
    items.push({
      id: 'api-keys-expired',
      tone: 'attention',
      title: `${countOf(expired.length, 'API key')} expired`,
      detail: 'Programs using them can no longer sign in. Revoke them, or make new ones.',
      to: '/api-keys',
    })
  }

  const awaiting = inputs.vmCredentials?.filter((c) => c.awaiting_pickup) ?? []
  if (awaiting.length) {
    items.push({
      id: 'vm-credentials-awaiting',
      tone: 'attention',
      title: `${countOf(awaiting.length, 'VM credential')} waiting to be picked up`,
      detail: 'Issued, and not yet taken by the VM it is for.',
      to: '/vm-credentials',
    })
  }

  const erasures = inputs.erasures?.erasures ?? []
  const held = erasures.filter((e) => e.legal_hold)
  const soon = erasures.filter(
    (e) =>
      e.state === 'scheduled' &&
      !e.legal_hold &&
      e.erase_at !== null &&
      daysUntil(e.erase_at, now) <= ERASURE_WARNING_DAYS,
  )
  if (soon.length) {
    items.push({
      id: 'erasures-soon',
      tone: 'attention',
      title: `${countOf(soon.length, 'VM')} erased within ${ERASURE_WARNING_DAYS} days`,
      detail: 'Export or hold anything that must be kept before then.',
      to: '/erasures',
    })
  }
  if (held.length) {
    items.push({
      id: 'erasures-held',
      tone: 'attention',
      title: `${countOf(held.length, 'erasure')} on legal hold`,
      detail: 'Kept past their date until the hold is lifted.',
      to: '/erasures',
    })
  }

  const denied = inputs.audit?.events.filter((e) => e.outcome === 'denied') ?? []
  if (denied.length) {
    items.push({
      id: 'audit-denied',
      tone: 'attention',
      title: `${countOf(denied.length, 'recent request')} denied`,
      detail: denied
        .map((e) => `${e.actor_email ?? 'someone'}: ${e.method} ${e.route}`)
        .join(' · '),
      to: '/compliance',
    })
  }

  return items
}
