import { describe, expect, it } from 'vitest'
import { formatTokens } from '../formatTokens'

describe('formatTokens', () => {
  it('abbreviates millions and thousands to one decimal', () => {
    expect(formatTokens(2_500_000)).toBe('2.5M')
    expect(formatTokens(1_000)).toBe('1.0K')
  })

  it('leaves counts under a thousand as they are', () => {
    expect(formatTokens(999)).toBe((999).toLocaleString())
  })
})
