import { type ReactNode } from "react";
import { ParentSize } from "@visx/responsive";

interface ChartContainerProps {
  children: (width: number, height: number) => ReactNode;
  minHeight?: number;
}

export default function ChartContainer({
  children,
  minHeight = 300,
}: ChartContainerProps) {
  return (
    <div style={{ width: "100%", minHeight }}>
      <ParentSize>
        {({ width, height }) => {
          if (width === 0 || height === 0) return null;
          return <>{children(width, height)}</>;
        }}
      </ParentSize>
    </div>
  );
}
