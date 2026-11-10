import { useMemo, useCallback, useId } from 'react'
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
import type { UsageDataPoint } from '../../types'
import { formatTokens } from '../../utils/formatTokens'
import { MarkerShape } from './MarkerShape'
import { type ChartSeries, nearestPoint, resolveSeries } from './chartSeries'

const margin = { top: 20, right: 20, bottom: 40, left: 60 }

/** A lone area is filled more strongly than overlapping ones, which would muddy each other. */
const SINGLE_FILL_OPACITY = { from: 0.3, to: 0.05 }
const MULTI_FILL_OPACITY = { from: 0.2, to: 0.03 }

interface TooltipData {
  date: Date
  values: { label: string; value: number; color: string }[]
}

function formatDate(d: Date): string {
  return d.toLocaleDateString(undefined, { month: 'short', day: 'numeric' })
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
  domainDays: number
  singleLabel: string
}

function AreaChartInner({ data, width, height, dataKey, series, domainDays, singleLabel }: AreaChartInnerProps) {
  const innerWidth = width - margin.left - margin.right
  const innerHeight = height - margin.top - margin.bottom
  // Gradients are referenced by id across the document, so two charts on
  // one page must not share them.
  const gradientPrefix = `area-gradient-${useId().replace(/[^a-zA-Z0-9]/g, '')}`

  const {
    showTooltip,
    hideTooltip,
    tooltipData,
    tooltipLeft,
    tooltipTop,
    tooltipOpen,
  } = useTooltip<TooltipData>()

  const isMultiSeries = series !== undefined && series.length > 0
  const fillOpacity = isMultiSeries ? MULTI_FILL_OPACITY : SINGLE_FILL_OPACITY

  // tooltipOnly series feed the hover card but must not stretch the y-axis
  // (they're typically derived totals that would dominate the scale).
  // Memoized so `yScale` below does not rescan every point on each render,
  // including every mouse move while a tooltip is open.
  const allSeries = useMemo(() => resolveSeries(data, series, singleLabel), [data, series, singleLabel])
  const visibleSeries = useMemo(() => allSeries.filter((s) => !s.tooltipOnly), [allSeries])
  const allData = useMemo(
    () => (visibleSeries.length > 0 ? visibleSeries.flatMap((s) => s.data) : data),
    [visibleSeries, data],
  )

  const xScale = useMemo(
    () => {
      const end = new Date()
      end.setHours(0, 0, 0, 0)
      const start = new Date(end)
      start.setDate(start.getDate() - domainDays)
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
      const values = allSeries.map((s) => {
        const d = nearestPoint(s.data, x0)
        return { label: s.label, value: d ? Number(d[dataKey]) : 0, color: s.color }
      })
      const closest = nearestPoint(data, x0)

      showTooltip({
        tooltipData: { date: closest ? new Date(closest.date) : x0, values },
        tooltipLeft: point.x,
        tooltipTop: point.y,
      })
    },
    [xScale, data, allSeries, dataKey, showTooltip],
  )

  if (innerWidth <= 0 || innerHeight <= 0) return null

  return (
    <div style={{ position: 'relative' }}>
      <svg width={width} height={height} role="img" aria-label="Token usage area chart">
        {visibleSeries.map((s, i) => (
          <LinearGradient
            key={s.label}
            id={`${gradientPrefix}-${i}`}
            from={s.color}
            to={s.color}
            fromOpacity={fillOpacity.from}
            toOpacity={fillOpacity.to}
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

          {visibleSeries.map((s, i) => (
            <g key={s.label}>
              <AreaClosed
                data={s.data}
                x={(d) => xScale(new Date(d.date))}
                y={(d) => yScale(Number(d[dataKey]))}
                yScale={yScale}
                fill={`url(#${gradientPrefix}-${i})`}
                curve={curveMonotoneX}
              />
              <LinePath
                data={s.data}
                x={(d) => xScale(new Date(d.date))}
                y={(d) => yScale(Number(d[dataKey]))}
                stroke={s.color}
                strokeWidth={2}
                curve={curveMonotoneX}
              />
            </g>
          ))}

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
              {visibleSeries.map((s, i) => {
                const d = nearestPoint(s.data, tooltipData.date)
                if (!d) return null
                const cx = xScale(new Date(d.date))
                const cy = yScale(Number(d[dataKey]))
                return isMultiSeries ? (
                  <MarkerShape key={s.label} seriesIndex={i} cx={cx} cy={cy} color={s.color} />
                ) : (
                  <Circle
                    key={s.label}
                    cx={cx}
                    cy={cy}
                    r={4}
                    fill={s.color}
                    stroke="var(--bgColor-default, #0d1117)"
                    strokeWidth={2}
                  />
                )
              })}
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
          {isMultiSeries && (
            <g>
              {visibleSeries.map((s, i) => (
                <g key={s.label} transform={`translate(${innerWidth - 140}, ${i * 20 + 4})`}>
                  <line x1={0} y1={0} x2={16} y2={0} stroke={s.color} strokeWidth={2} />
                  <MarkerShape seriesIndex={i} cx={8} cy={0} color={s.color} />
                  <text x={22} y={0} dominantBaseline="central" fontSize={11} fill="var(--fgColor-muted, #8b949e)">
                    {s.label}
                  </text>
                </g>
              ))}
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
  singleLabel = 'Tokens',
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
