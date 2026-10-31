import { LinePath } from "@visx/shape";
import { scaleTime, scaleLinear } from "@visx/scale";
import { AxisBottom, AxisLeft } from "@visx/axis";
import { GridRows, GridColumns } from "@visx/grid";
import { Group } from "@visx/group";
import { curveMonotoneX } from "@visx/curve";
import { extent, max } from "d3-array";

const DEFAULT_MARGIN = { top: 20, right: 20, bottom: 40, left: 50 };
const DEFAULT_COLOR = "#0969da";

interface LineChartProps<T> {
  data: T[];
  xAccessor: (d: T) => Date;
  yAccessor: (d: T) => number;
  width: number;
  height: number;
  color?: string;
  margin?: { top: number; right: number; bottom: number; left: number };
}

export default function LineChart<T>({
  data,
  xAccessor,
  yAccessor,
  width,
  height,
  color = DEFAULT_COLOR,
  margin = DEFAULT_MARGIN,
}: LineChartProps<T>) {
  const innerWidth = width - margin.left - margin.right;
  const innerHeight = height - margin.top - margin.bottom;

  if (innerWidth <= 0 || innerHeight <= 0) return null;

  const xScale = scaleTime({
    domain: extent(data, xAccessor) as [Date, Date],
    range: [0, innerWidth],
  });

  const yScale = scaleLinear({
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
        <GridColumns
          scale={xScale}
          height={innerHeight}
          stroke="#e0e0e0"
          strokeOpacity={0.5}
        />
        <LinePath<T>
          data={data}
          x={(d) => xScale(xAccessor(d)) ?? 0}
          y={(d) => yScale(yAccessor(d)) ?? 0}
          curve={curveMonotoneX}
          stroke={color}
          strokeWidth={2}
          strokeLinecap="round"
        />
        <AxisBottom
          top={innerHeight}
          scale={xScale}
          numTicks={Math.min(data.length, 6)}
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
