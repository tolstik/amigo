import type { EChartsOption } from "echarts";
import * as echarts from "echarts/core";
import { BarChart, LineChart, ScatterChart } from "echarts/charts";
import { AriaComponent, DataZoomComponent, GridComponent, LegendComponent, TooltipComponent } from "echarts/components";
import { CanvasRenderer } from "echarts/renderers";
import ReactEChartsCore from "echarts-for-react/lib/core";
import { useMemo, type ReactNode } from "react";
import { chartOptionForTheme } from "../charts/theme";
import { useColorScheme } from "../hooks/useColorScheme";
import { useTheme } from "../theme/ThemeProvider";

echarts.use([
  LineChart,
  BarChart,
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
  const { theme } = useTheme();
  const themedOption = useMemo(() => chartOptionForTheme(option, theme), [option, theme]);
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
          key={theme}
          echarts={echarts}
          option={{ ...themedOption, backgroundColor: "transparent", aria: { enabled: true, decal: { show: true } } }}
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
