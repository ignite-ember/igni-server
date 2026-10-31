import { useMemo, useCallback } from 'react'
import { Group } from '@visx/group'
import { AreaClosed, LinePath, Circle } from '@visx/shape'
import { scaleTime, scaleLinear } from '@visx/scale'
import { AxisBottom, AxisLeft } from '@visx/axis'
import { GridRows } from '@visx/grid'
import { LinearGradient } from '@visx/gradient'
import { ParentSize } from '@visx/responsive'
import { curveMonotoneX } from '@visx/curve'
import { useTooltip, TooltipWithBounds, defaultStyles } from '@visx/tooltip'
import { localPoint } from '@visx/event'
import { bisector } from 'd3-array'
import type { UsageDataPoint } from '../../types'

const margin = { top: 20, right: 20, bottom: 40, left: 60 }

const SERIES_COLORS = [
  '#0969da', '#1a7f37', '#cf222e', '#bf8700', '#8250df',
  '#da3633', '#0550ae', '#116329', '#953800', '#57606a',
]

// Marker shapes for multi-series differentiation
const MARKER_SHAPES = ['circle', 'diamond', 'square', 'triangle', 'cross'] as const

export interface ChartSeries {
  label: string
  data: UsageDataPoint[]
  color?: string
  /** If true, show this series in the hover tooltip only — don't draw a line/area or take a legend slot. */
  tooltipOnly?: boolean
}

interface TooltipData {
  date: Date
  values: { label: string; value: number; color: string }[]
}

const bisectDate = bisector<UsageDataPoint, Date>((d) => new Date(d.date)).left

function formatTokens(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}K`
  return n.toLocaleString()
}

function formatDate(d: Date): string {
  return d.toLocaleDateString(undefined, { month: 'short', day: 'numeric' })
}

function MarkerShape({ shape, cx, cy, color }: { shape: typeof MARKER_SHAPES[number]; cx: number; cy: number; color: string }) {
  const size = 4
  switch (shape) {
    case 'circle':
      return <Circle cx={cx} cy={cy} r={size} fill={color} />
    case 'diamond':
      return <polygon points={`${cx},${cy - size} ${cx + size},${cy} ${cx},${cy + size} ${cx - size},${cy}`} fill={color} />
    case 'square':
      return <rect x={cx - size} y={cy - size} width={size * 2} height={size * 2} fill={color} />
    case 'triangle':
      return <polygon points={`${cx},${cy - size} ${cx + size},${cy + size} ${cx - size},${cy + size}`} fill={color} />
    case 'cross':
      return (
        <g>
          <line x1={cx - size} y1={cy} x2={cx + size} y2={cy} stroke={color} strokeWidth={2} />
          <line x1={cx} y1={cy - size} x2={cx} y2={cy + size} stroke={color} strokeWidth={2} />
        </g>
      )
  }
}

const tooltipStyles = {
  ...defaultStyles,
  backgroundColor: 'var(--bgColor-emphasis, #1c2128)',
  color: 'var(--fgColor-onEmphasis, #f0f6fc)',
  border: '1px solid var(--borderColor-default, #30363d)',
  borderRadius: '6px',
  padding: '8px 12px',
  fontSize: '12px',
  lineHeight: '1.5',
  boxShadow: 'var(--shadow-resting-medium, 0 3px 6px rgba(0,0,0,0.3))',
}

interface AreaChartInnerProps {
  data: UsageDataPoint[]
  width: number
  height: number
  dataKey: keyof UsageDataPoint
  series?: ChartSeries[]
  domainDays?: number
  singleLabel?: string
}

function AreaChartInner({ data, width, height, dataKey, series, domainDays, singleLabel }: AreaChartInnerProps) {
  const innerWidth = width - margin.left - margin.right
  const innerHeight = height - margin.top - margin.bottom

  const {
    showTooltip,
    hideTooltip,
    tooltipData,
    tooltipLeft,
    tooltipTop,
    tooltipOpen,
  } = useTooltip<TooltipData>()

  // tooltipOnly series feed the hover card but must not stretch the y-axis
  // (they're typically derived totals that would dominate the scale).
  //
  // Both memoized, and not for tidiness: they were computed during render,
  // so each was a new array every time, and the `yScale` memo below —
  // which lists `allData` as a dependency — therefore recomputed
  // `Math.max` across every point on every render, including every mouse
  // move while a tooltip is open. eslint reported it as "existing
  // memoization could not be preserved", which is the React Compiler
  // saying the same thing: a dependency that is rebuilt each render
  // makes the memo around it decorative.
  const visibleSeries = useMemo(
    () => (series ? series.filter((s) => !s.tooltipOnly) : undefined),
    [series],
  )
  const allData = useMemo(
    () => (visibleSeries && visibleSeries.length > 0 ? visibleSeries.flatMap((s) => s.data) : data),
    [visibleSeries, data],
  )

  const xScale = useMemo(
    () => {
      const end = new Date()
      end.setHours(0, 0, 0, 0)
      const start = new Date(end)
      start.setDate(start.getDate() - (domainDays ?? 30))
      return scaleTime({
        domain: [start, end],
        range: [0, innerWidth],
      })
    },
    [innerWidth, domainDays],
  )

  const yScale = useMemo(
    () =>
      scaleLinear({
        domain: [0, Math.max(...allData.map((d) => Number(d[dataKey])), 1)],
        range: [innerHeight, 0],
        nice: true,
      }),
    [allData, innerHeight, dataKey],
  )

  const handleTooltip = useCallback(
    (event: React.TouchEvent<SVGRectElement> | React.MouseEvent<SVGRectElement>) => {
      const point = localPoint(event)
      if (!point) return

      const x0 = xScale.invert(point.x - margin.left)
      const hasSeries = series && series.length > 0

      if (hasSeries) {
        const values = series.map((s, i) => {
          const idx = bisectDate(s.data, x0, 1)
          const d0 = s.data[idx - 1]
          const d1 = s.data[idx]
          const d = d1 && x0.getTime() - new Date(d0?.date).getTime() > new Date(d1.date).getTime() - x0.getTime()
            ? d1
            : d0
          return {
            label: s.label,
            value: d ? Number(d[dataKey]) : 0,
            color: s.color ?? SERIES_COLORS[i % SERIES_COLORS.length],
          }
        })

        const idx = bisectDate(data, x0, 1)
        const d0 = data[idx - 1]
        const d1 = data[idx]
        const closest = d1 && x0.getTime() - new Date(d0?.date).getTime() > new Date(d1.date).getTime() - x0.getTime()
          ? d1
          : d0

        showTooltip({
          tooltipData: { date: closest ? new Date(closest.date) : x0, values },
          tooltipLeft: point.x,
          tooltipTop: point.y,
        })
      } else {
        const idx = bisectDate(data, x0, 1)
        const d0 = data[idx - 1]
        const d1 = data[idx]
        const d = d1 && x0.getTime() - new Date(d0?.date).getTime() > new Date(d1.date).getTime() - x0.getTime()
          ? d1
          : d0

        if (d) {
          showTooltip({
            tooltipData: {
              date: new Date(d.date),
              values: [{ label: singleLabel ?? 'Tokens', value: Number(d[dataKey]), color: '#1f6feb' }],
            },
            tooltipLeft: point.x,
            tooltipTop: point.y,
          })
        }
      }
    },
    [xScale, data, series, dataKey, showTooltip, singleLabel],
  )

  if (innerWidth <= 0 || innerHeight <= 0) return null

  const hasSeries = series && series.length > 0

  return (
    <div style={{ position: 'relative' }}>
      <svg width={width} height={height} role="img" aria-label="Token usage area chart">
        {!hasSeries && (
          <LinearGradient
            id="area-gradient"
            from="#1f6feb"
            to="#1f6feb"
            fromOpacity={0.3}
            toOpacity={0.05}
          />
        )}
        {hasSeries && visibleSeries!.map((s, i) => (
          <LinearGradient
            key={s.label}
            id={`area-gradient-${i}`}
            from={s.color ?? SERIES_COLORS[i % SERIES_COLORS.length]}
            to={s.color ?? SERIES_COLORS[i % SERIES_COLORS.length]}
            fromOpacity={0.2}
            toOpacity={0.03}
          />
        ))}
        <Group left={margin.left} top={margin.top}>
          <GridRows
            scale={yScale}
            width={innerWidth}
            strokeDasharray="3,3"
            stroke="var(--borderColor-muted, #30363d)"
            strokeOpacity={0.6}
            numTicks={5}
          />

          {hasSeries ? (
            <>
              {visibleSeries!.map((s, i) => {
                const color = s.color ?? SERIES_COLORS[i % SERIES_COLORS.length]
                return (
                  <g key={s.label}>
                    <AreaClosed
                      data={s.data}
                      x={(d) => xScale(new Date(d.date))}
                      y={(d) => yScale(Number(d[dataKey]))}
                      yScale={yScale}
                      fill={`url(#area-gradient-${i})`}
                      curve={curveMonotoneX}
                    />
                    <LinePath
                      data={s.data}
                      x={(d) => xScale(new Date(d.date))}
                      y={(d) => yScale(Number(d[dataKey]))}
                      stroke={color}
                      strokeWidth={2}
                      curve={curveMonotoneX}
                    />
                  </g>
                )
              })}
            </>
          ) : (
            <>
              <AreaClosed
                data={data}
                x={(d) => xScale(new Date(d.date))}
                y={(d) => yScale(Number(d[dataKey]))}
                yScale={yScale}
                fill="url(#area-gradient)"
                curve={curveMonotoneX}
              />
              <LinePath
                data={data}
                x={(d) => xScale(new Date(d.date))}
                y={(d) => yScale(Number(d[dataKey]))}
                stroke="#1f6feb"
                strokeWidth={2}
                curve={curveMonotoneX}
              />
            </>
          )}

          {/* Tooltip crosshair + markers */}
          {tooltipOpen && tooltipData && (
            <>
              <line
                x1={xScale(tooltipData.date)}
                y1={0}
                x2={xScale(tooltipData.date)}
                y2={innerHeight}
                stroke="var(--borderColor-default, #484f58)"
                strokeWidth={1}
                strokeDasharray="4,4"
                pointerEvents="none"
              />
              {hasSeries
                ? visibleSeries!.map((s, i) => {
                    const color = s.color ?? SERIES_COLORS[i % SERIES_COLORS.length]
                    const idx = bisectDate(s.data, tooltipData.date, 1)
                    const d0 = s.data[idx - 1]
                    const d1 = s.data[idx]
                    const d = d1 && tooltipData.date.getTime() - new Date(d0?.date).getTime() > new Date(d1.date).getTime() - tooltipData.date.getTime()
                      ? d1
                      : d0
                    if (!d) return null
                    const cx = xScale(new Date(d.date))
                    const cy = yScale(Number(d[dataKey]))
                    return (
                      <MarkerShape
                        key={s.label}
                        shape={MARKER_SHAPES[i % MARKER_SHAPES.length]}
                        cx={cx}
                        cy={cy}
                        color={color}
                      />
                    )
                  })
                : (() => {
                    const idx = bisectDate(data, tooltipData.date, 1)
                    const d0 = data[idx - 1]
                    const d1 = data[idx]
                    const d = d1 && tooltipData.date.getTime() - new Date(d0?.date).getTime() > new Date(d1.date).getTime() - tooltipData.date.getTime()
                      ? d1
                      : d0
                    if (!d) return null
                    return (
                      <Circle
                        cx={xScale(new Date(d.date))}
                        cy={yScale(Number(d[dataKey]))}
                        r={4}
                        fill="#1f6feb"
                        stroke="var(--bgColor-default, #0d1117)"
                        strokeWidth={2}
                      />
                    )
                  })()
              }
            </>
          )}

          <AxisBottom
            top={innerHeight}
            scale={xScale}
            numTicks={6}
            tickLabelProps={() => ({
              fill: 'var(--fgColor-muted, #8b949e)',
              fontSize: 11,
              textAnchor: 'middle' as const,
            })}
            stroke="var(--borderColor-default, #30363d)"
            tickStroke="var(--borderColor-default, #30363d)"
          />
          <AxisLeft
            scale={yScale}
            numTicks={5}
            tickFormat={(v) => formatTokens(Number(v))}
            tickLabelProps={() => ({
              fill: 'var(--fgColor-muted, #8b949e)',
              fontSize: 11,
              textAnchor: 'end' as const,
              dx: -4,
              dy: 4,
            })}
            stroke="var(--borderColor-default, #30363d)"
            tickStroke="var(--borderColor-default, #30363d)"
          />

          {/* Invisible overlay for tooltip interaction */}
          <rect
            width={innerWidth}
            height={innerHeight}
            fill="transparent"
            onMouseMove={handleTooltip}
            onTouchMove={handleTooltip}
            onMouseLeave={hideTooltip}
            onTouchEnd={hideTooltip}
          />

          {/* Legend for multi-series — tooltipOnly series omitted */}
          {hasSeries && (
            <g>
              {visibleSeries!.map((s, i) => {
                const color = s.color ?? SERIES_COLORS[i % SERIES_COLORS.length]
                const markerShape = MARKER_SHAPES[i % MARKER_SHAPES.length]
                return (
                  <g key={s.label} transform={`translate(${innerWidth - 140}, ${i * 20 + 4})`}>
                    <line
                      x1={0}
                      y1={0}
                      x2={16}
                      y2={0}
                      stroke={color}
                      strokeWidth={2}
                    />
                    <MarkerShape shape={markerShape} cx={8} cy={0} color={color} />
                    <text x={22} y={0} dominantBaseline="central" fontSize={11} fill="var(--fgColor-muted, #8b949e)">
                      {s.label}
                    </text>
                  </g>
                )
              })}
            </g>
          )}
        </Group>
      </svg>

      {/* Tooltip */}
      {tooltipOpen && tooltipData && (
        <TooltipWithBounds
          left={tooltipLeft}
          top={tooltipTop}
          style={tooltipStyles}
        >
          <div style={{ fontWeight: 600, marginBottom: 4 }}>
            {formatDate(tooltipData.date)}
          </div>
          {tooltipData.values.map((v) => (
            <div key={v.label} style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
              <span style={{ width: 8, height: 8, borderRadius: '50%', backgroundColor: v.color, flexShrink: 0 }} />
              <span>{v.label}:</span>
              <span style={{ fontWeight: 600 }}>{formatTokens(v.value)}</span>
            </div>
          ))}
        </TooltipWithBounds>
      )}
    </div>
  )
}

interface AreaChartProps {
  data: UsageDataPoint[]
  height?: number
  dataKey?: keyof UsageDataPoint
  series?: ChartSeries[]
  domainDays?: number
  singleLabel?: string
}

export function AreaChart({
  data,
  height = 300,
  dataKey = 'total_tokens',
  series,
  domainDays = 30,
  singleLabel,
}: AreaChartProps) {
  if (!data || data.length === 0) return null

  return (
    <ParentSize>
      {({ width }) => (
        <AreaChartInner
          data={data}
          width={width}
          height={height}
          dataKey={dataKey}
          series={series}
          domainDays={domainDays}
          singleLabel={singleLabel}
        />
      )}
    </ParentSize>
  )
}
