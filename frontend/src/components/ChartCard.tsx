import type { EChartsOption } from "echarts";
import * as echarts from "echarts/core";
import { LineChart, ScatterChart } from "echarts/charts";
import { AriaComponent, DataZoomComponent, GridComponent, LegendComponent, TooltipComponent } from "echarts/components";
import { CanvasRenderer } from "echarts/renderers";
import ReactEChartsCore from "echarts-for-react/lib/core";
import type { ReactNode } from "react";
import { useColorScheme } from "../hooks/useColorScheme";

echarts.use([
  LineChart,
  ScatterChart,
  AriaComponent,
  DataZoomComponent,
  GridComponent,
  LegendComponent,
  TooltipComponent,
  CanvasRenderer,
]);

interface ChartCardProps {
  title: string;
  subtitle?: string;
  option: EChartsOption;
  ariaLabel: string;
  height?: number;
  aside?: ReactNode;
  footer?: ReactNode;
}

export function ChartCard({ title, subtitle, option, ariaLabel, height = 390, aside, footer }: ChartCardProps) {
  const scheme = useColorScheme();
  return (
    <section className="panel chart-card">
      <div className="panel__head">
        <div>
          <h2>{title}</h2>
          {subtitle && <p>{subtitle}</p>}
        </div>
        {aside}
      </div>
      <div role="img" aria-label={ariaLabel}>
        <ReactEChartsCore
          echarts={echarts}
          option={{ ...option, backgroundColor: "transparent", aria: { enabled: true, decal: { show: true } } }}
          notMerge
          lazyUpdate
          style={{ height }}
          theme={scheme}
        />
      </div>
      {footer}
    </section>
  );
}
