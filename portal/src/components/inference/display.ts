import type { HealthState, NodeStatus } from '../../types'

/** `unknown` shares amber with `degraded`: "not checked yet" is nothing an operator can act on. */
export const HEALTH_TONE: Record<HealthState, 'ok' | 'warn' | 'bad'> = {
  healthy: 'ok',
  degraded: 'warn',
  unknown: 'warn',
  unreachable: 'bad',
}

export const HEALTH_LABEL: Record<HealthState, string> = {
  healthy: 'Reachable',
  degraded: 'Degraded',
  unknown: 'Not yet checked',
  unreachable: 'Unreachable',
}

/** Where a node came from, and therefore where to go to change it. */
export const SOURCE_DETAIL: Record<NodeStatus['source'], string> = {
  static: 'this deployment’s configuration — change it there and redeploy',
  registered: 'the node itself, at startup — change it in whatever starts the cluster',
  pinned: 'someone here, in the portal',
}

export function percent(ratio: number): string {
  return `${Math.round(ratio * 100)}%`
}

/** `count` as a share of a node's slots, 0 for a node with none. */
export function occupancy(count: number, maxConcurrent: number): number {
  return maxConcurrent > 0 ? count / maxConcurrent : 0
}

/**
 * An age as the server measured it, not rebuilt into a timestamp: that
 * would fold the browser's clock skew into a liveness decision.
 */
export function formatAge(seconds: number): string {
  if (seconds < 60) return `${Math.round(seconds)}s ago`
  if (seconds < 3600) return `${Math.round(seconds / 60)}m ago`
  return `${Math.round(seconds / 3600)}h ago`
}

/** An instant the server recorded once, in the reader's own locale. */
export function formatWhen(iso: string): string {
  const at = new Date(iso)
  return Number.isNaN(at.getTime()) ? 'at an unknown time' : `on ${at.toLocaleString()}`
}

/**
 * Load colour by headroom, as a Primer token *name*: ProgressBar builds
 * the `var(--bgColor-…)` itself, and a finished `var(...)` renders grey.
 */
export function loadColor(ratio: number): string {
  if (ratio >= 0.9) return 'danger.emphasis'
  if (ratio >= 0.7) return 'attention.emphasis'
  return 'success.emphasis'
}
