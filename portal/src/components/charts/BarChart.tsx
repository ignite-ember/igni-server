import { Bar } from "@visx/shape";
import { scaleBand, scaleLinear } from "@visx/scale";
import { AxisBottom, AxisLeft } from "@visx/axis";
import { GridRows } from "@visx/grid";
import { Group } from "@visx/group";
import { max } from "d3-array";

const DEFAULT_MARGIN = { top: 20, right: 20, bottom: 40, left: 50 };
const DEFAULT_COLOR = "#0969da";

interface BarChartProps<T> {
  data: T[];
  xAccessor: (d: T) => string;
  yAccessor: (d: T) => number;
  width: number;
  height: number;
  color?: string;
  margin?: { top: number; right: number; bottom: number; left: number };
}

export default function BarChart<T>({
  data,
  xAccessor,
  yAccessor,
  width,
  height,
  color = DEFAULT_COLOR,
  margin = DEFAULT_MARGIN,
}: BarChartProps<T>) {
  const innerWidth = width - margin.left - margin.right;
  const innerHeight = height - margin.top - margin.bottom;

  if (innerWidth <= 0 || innerHeight <= 0) return null;

  const xScale = scaleBand<string>({
    domain: data.map(xAccessor),
    range: [0, innerWidth],
    padding: 0.3,
  });

  const yScale = scaleLinear<number>({
    domain: [0, max(data, yAccessor) ?? 0],
    range: [innerHeight, 0],
    nice: true,
  });

  return (
    <svg width={width} height={height}>
      <Group left={margin.left} top={margin.top}>
        <GridRows
          scale={yScale}
          width={innerWidth}
          stroke="#e0e0e0"
          strokeOpacity={0.5}
        />
        {data.map((d, i) => {
          const xVal = xAccessor(d);
          const barWidth = xScale.bandwidth();
          const barHeight = innerHeight - (yScale(yAccessor(d)) ?? 0);
          const barX = xScale(xVal) ?? 0;
          const barY = innerHeight - barHeight;

          return (
            <Bar
              key={`bar-${i}`}
              x={barX}
              y={barY}
              width={barWidth}
              height={barHeight}
              fill={color}
              rx={2}
            />
          );
        })}
        <AxisBottom
          top={innerHeight}
          scale={xScale}
          tickLabelProps={() => ({
            fontSize: 11,
            textAnchor: "middle" as const,
            fill: "#666",
          })}
        />
        <AxisLeft
          scale={yScale}
          numTicks={5}
          tickLabelProps={() => ({
            fontSize: 11,
            textAnchor: "end" as const,
            dx: -4,
            dy: 4,
            fill: "#666",
          })}
        />
      </Group>
    </svg>
  );
}
