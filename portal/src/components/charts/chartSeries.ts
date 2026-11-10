import { bisector } from 'd3-array'
import type { UsageDataPoint } from '../../types'

const SERIES_COLORS = [
  '#0969da', '#1a7f37', '#cf222e', '#bf8700', '#8250df',
  '#da3633', '#0550ae', '#116329', '#953800', '#57606a',
]

/** The colour a chart without `series` draws its one line in. */
export const SINGLE_SERIES_COLOR = '#1f6feb'

export interface ChartSeries {
  label: string
  data: UsageDataPoint[]
  color?: string
  /** If true, show this series in the hover tooltip only — don't draw a line/area or take a legend slot. */
  tooltipOnly?: boolean
}

/** A series with its colour settled, so nothing downstream recomputes the fallback. */
export interface ResolvedSeries {
  label: string
  data: UsageDataPoint[]
  color: string
  tooltipOnly: boolean
}

/**
 * The series a chart draws. Without `series`, `data` becomes the one
 * series, so single- and multi-series charts share one render path.
 */
export function resolveSeries(
  data: UsageDataPoint[],
  series: ChartSeries[] | undefined,
  singleLabel: string,
): ResolvedSeries[] {
  if (!series || series.length === 0) {
    return [{ label: singleLabel, data, color: SINGLE_SERIES_COLOR, tooltipOnly: false }]
  }
  return series.map((s, i) => ({
    label: s.label,
    data: s.data,
    color: s.color ?? SERIES_COLORS[i % SERIES_COLORS.length],
    tooltipOnly: s.tooltipOnly ?? false,
  }))
}

const bisectDate = bisector<UsageDataPoint, Date>((d) => new Date(d.date)).left

/** The point in date-sorted `data` closest to `date`, or undefined when `data` is empty. */
export function nearestPoint(data: UsageDataPoint[], date: Date): UsageDataPoint | undefined {
  const idx = bisectDate(data, date, 1)
  const before = data[idx - 1]
  const after = data[idx]
  if (!before || !after) return before ?? after
  const toBefore = date.getTime() - new Date(before.date).getTime()
  const toAfter = new Date(after.date).getTime() - date.getTime()
  return toBefore > toAfter ? after : before
}
