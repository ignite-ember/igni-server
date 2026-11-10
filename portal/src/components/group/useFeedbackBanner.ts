import { useCallback, useEffect, useRef, useState } from 'react'

import type { Feedback, ShowFeedback } from './feedback'

/**
 * A banner that clears itself after `timeoutMs`.
 *
 * One timer at a time: a newer message restarts it, so an older one's
 * timer cannot clear the newer banner early, and nothing fires after
 * the page has gone.
 */
export function useFeedbackBanner(timeoutMs = 5000): {
  banner: Feedback | null
  show: ShowFeedback
  dismiss: () => void
} {
  const [banner, setBanner] = useState<Feedback | null>(null)
  const timer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined)

  useEffect(() => () => clearTimeout(timer.current), [])

  const show = useCallback<ShowFeedback>(
    (variant, message) => {
      clearTimeout(timer.current)
      setBanner({ variant, message })
      timer.current = setTimeout(() => setBanner(null), timeoutMs)
    },
    [timeoutMs]
  )

  const dismiss = useCallback(() => {
    clearTimeout(timer.current)
    setBanner(null)
  }, [])

  return { banner, show, dismiss }
}
