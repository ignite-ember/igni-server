import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest'
import { act, renderHook } from '@testing-library/react'

import { useTransientFeedback } from '../useTransientFeedback'

describe('useTransientFeedback', () => {
  beforeEach(() => {
    vi.useFakeTimers()
  })

  afterEach(() => {
    vi.useRealTimers()
  })

  test('clears the message after the duration', () => {
    const { result } = renderHook(() => useTransientFeedback(1000))

    act(() => result.current.show('success', 'Saved'))
    expect(result.current.feedback).toEqual({ variant: 'success', message: 'Saved' })

    act(() => vi.advanceTimersByTime(1000))
    expect(result.current.feedback).toBeNull()
  })

  test('a newer message gets its own full duration', () => {
    // The old setTimeout-per-call cleared whatever was on screen when the
    // first timer fired, cutting a second message short.
    const { result } = renderHook(() => useTransientFeedback(1000))

    act(() => result.current.show('success', 'First'))
    act(() => vi.advanceTimersByTime(800))
    act(() => result.current.show('critical', 'Second'))
    act(() => vi.advanceTimersByTime(800))

    expect(result.current.feedback).toEqual({ variant: 'critical', message: 'Second' })
  })

  test('unmounting cancels the pending clear', () => {
    const { result, unmount } = renderHook(() => useTransientFeedback(1000))

    act(() => result.current.show('success', 'Saved'))
    unmount()

    expect(vi.getTimerCount()).toBe(0)
  })
})
