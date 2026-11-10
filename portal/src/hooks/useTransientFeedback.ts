import { useCallback, useEffect, useState } from 'react'

export type FeedbackVariant = 'success' | 'critical'

export interface Feedback {
  variant: FeedbackVariant
  message: string
}

/**
 * A banner message that clears itself after `durationMs`.
 *
 * The timer belongs to the message on screen: a newer message restarts
 * it, and unmounting cancels it, so an old timer never clears a newer
 * message early or fires into an unmounted page.
 */
export function useTransientFeedback(durationMs = 5000) {
  const [feedback, setFeedback] = useState<Feedback | null>(null)

  useEffect(() => {
    if (!feedback) return
    const timer = setTimeout(() => setFeedback(null), durationMs)
    return () => clearTimeout(timer)
  }, [feedback, durationMs])

  const show = useCallback((variant: FeedbackVariant, message: string) => setFeedback({ variant, message }), [])
  const dismiss = useCallback(() => setFeedback(null), [])

  return { feedback, show, dismiss }
}
