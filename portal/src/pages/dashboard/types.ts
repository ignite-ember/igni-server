import type { UsageDataPoint } from '../../types'

/** Whose usage a chart shows: yours, or the whole organisation's (admins). */
export type UsageScope = 'personal' | 'team'

/** The answer to `GET /portal/usage`. */
export interface UsageResponse {
  data: UsageDataPoint[]
  total_tokens: number
  total_input_tokens: number
  total_output_tokens: number
  total_requests: number
}

/** How much a thing on the "needs attention" list matters. */
export type AttentionTone = 'danger' | 'attention'

/** One thing someone should look at, and where to go to do it. */
export interface AttentionItem {
  id: string
  tone: AttentionTone
  title: string
  detail: string
  to: string
}
