import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest'
import { act, renderHook } from '@testing-library/react'

import { useFeedbackBanner } from '../group/useFeedbackBanner'

describe('useFeedbackBanner', () => {
  beforeEach(() => vi.useFakeTimers())
  afterEach(() => vi.useRealTimers())

  test('clears itself after the timeout', () => {
    const { result } = renderHook(() => useFeedbackBanner(1000))
    act(() => result.current.show('success', 'Saved'))
    expect(result.current.banner).toEqual({ variant: 'success', message: 'Saved' })
    act(() => vi.advanceTimersByTime(1000))
    expect(result.current.banner).toBeNull()
  })

  test("an older message's timer does not clear a newer one early", () => {
    const { result } = renderHook(() => useFeedbackBanner(1000))
    act(() => result.current.show('success', 'First'))
    act(() => vi.advanceTimersByTime(600))
    act(() => result.current.show('critical', 'Second'))
    act(() => vi.advanceTimersByTime(600))
    expect(result.current.banner).toEqual({ variant: 'critical', message: 'Second' })
  })

  test('nothing is left running after unmount', () => {
    const { result, unmount } = renderHook(() => useFeedbackBanner(1000))
    act(() => result.current.show('success', 'Saved'))
    unmount()
    expect(vi.getTimerCount()).toBe(0)
  })
})
