/** What a section of the app page reports to the page's one banner. */
export type FeedbackVariant = 'success' | 'critical'

export interface Feedback {
  variant: FeedbackVariant
  message: string
}

export type OnFeedback = (variant: FeedbackVariant, message: string) => void
