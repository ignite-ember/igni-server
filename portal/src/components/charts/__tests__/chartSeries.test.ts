import { describe, expect, it } from 'vitest'
import type { UsageDataPoint } from '../../../types'
import { SINGLE_SERIES_COLOR, nearestPoint, resolveSeries } from '../chartSeries'

function point(date: string, total_tokens = 0): UsageDataPoint {
  return { date, input_tokens: 0, output_tokens: 0, total_tokens, request_count: 0 }
}

const days = [point('2026-01-01'), point('2026-01-03'), point('2026-01-05')]

describe('nearestPoint', () => {
  it('picks whichever neighbour is closer', () => {
    expect(nearestPoint(days, new Date('2026-01-03T20:00:00'))).toBe(days[1])
    expect(nearestPoint(days, new Date('2026-01-04T20:00:00'))).toBe(days[2])
  })

  it('clamps to the ends of the data', () => {
    expect(nearestPoint(days, new Date('2025-12-01'))).toBe(days[0])
    expect(nearestPoint(days, new Date('2026-02-01'))).toBe(days[2])
  })

  it('finds nothing in no data', () => {
    expect(nearestPoint([], new Date('2026-01-01'))).toBeUndefined()
  })
})

describe('resolveSeries', () => {
  it('turns bare data into one series', () => {
    expect(resolveSeries(days, undefined, 'Requests')).toEqual([
      { label: 'Requests', data: days, color: SINGLE_SERIES_COLOR, tooltipOnly: false },
    ])
  })

  it('keeps a given colour and gives the rest a palette colour by position', () => {
    const [given, fallback] = resolveSeries(days, [
      { label: 'Total', data: days, tooltipOnly: true, color: '#123456' },
      { label: 'Input', data: days },
    ], 'unused')
    expect(given.color).toBe('#123456')
    expect(given.tooltipOnly).toBe(true)
    expect(fallback.color).toBe('#1a7f37')
  })
})
