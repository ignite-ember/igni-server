/** How a change the page just made turned out, said in the banner above the tabs. */
export type FeedbackVariant = 'success' | 'critical'

export interface Feedback {
  variant: FeedbackVariant
  message: string
}

export type ShowFeedback = (variant: FeedbackVariant, message: string) => void
