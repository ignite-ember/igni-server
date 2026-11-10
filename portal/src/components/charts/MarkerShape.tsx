import { Circle } from '@visx/shape'

/** Marker shapes that tell series apart without relying on colour alone. */
const MARKER_SHAPES = ['circle', 'diamond', 'square', 'triangle', 'cross'] as const

const SIZE = 4

interface MarkerShapeProps {
  /** Position of the series among those drawn; the shape cycles with it. */
  seriesIndex: number
  cx: number
  cy: number
  color: string
}

export function MarkerShape({ seriesIndex, cx, cy, color }: MarkerShapeProps) {
  switch (MARKER_SHAPES[seriesIndex % MARKER_SHAPES.length]) {
    case 'circle':
      return <Circle cx={cx} cy={cy} r={SIZE} fill={color} />
    case 'diamond':
      return <polygon points={`${cx},${cy - SIZE} ${cx + SIZE},${cy} ${cx},${cy + SIZE} ${cx - SIZE},${cy}`} fill={color} />
    case 'square':
      return <rect x={cx - SIZE} y={cy - SIZE} width={SIZE * 2} height={SIZE * 2} fill={color} />
    case 'triangle':
      return <polygon points={`${cx},${cy - SIZE} ${cx + SIZE},${cy + SIZE} ${cx - SIZE},${cy + SIZE}`} fill={color} />
    case 'cross':
      return (
        <g>
          <line x1={cx - SIZE} y1={cy} x2={cx + SIZE} y2={cy} stroke={color} strokeWidth={2} />
          <line x1={cx} y1={cy - SIZE} x2={cx} y2={cy + SIZE} stroke={color} strokeWidth={2} />
        </g>
      )
  }
}
