import type { EChartsOption, LineSeriesOption } from "echarts";
import type {
  CompositionPoint,
  PressurePoint,
  WeightPlanPoint,
  WeightPoint,
  WeightProjectionPoint,
} from "../api/types";
import { formatNumber, formatShortDate } from "../lib/format";

const colors = {
  green: "#2d9365",
  greenSoft: "#77c69d",
  blue: "#4b7bec",
  violet: "#8b6ee8",
  coral: "#e9785d",
  amber: "#d99b35",
  muted: "#879288",
  grid: "rgba(128, 145, 134, .16)",
};

const sharedGrid = { left: 12, right: 18, top: 54, bottom: 52, containLabel: true };
const sharedAxis = {
  axisLine: { lineStyle: { color: colors.grid } },
  axisTick: { show: false },
  axisLabel: { color: colors.muted, hideOverlap: true },
  splitLine: { lineStyle: { color: colors.grid } },
};

function tooltipDate(value: unknown): string {
  if (typeof value !== "string" && typeof value !== "number") return "";
  return formatShortDate(new Date(value).toISOString());
}

function tooltipFormatter(params: any): string {
  const entries = Array.isArray(params) ? params : [params];
  if (!entries.length) return "";
  const dateValue = entries[0]?.axisValue ?? entries[0]?.value?.[0];
  const rows = entries
    .filter((entry: any) => {
      const value = Array.isArray(entry.value) ? entry.value[1] : entry.value;
      return value !== null && value !== undefined && value !== "-";
    })
    .map((entry: any) => {
      const value = Array.isArray(entry.value) ? entry.value[1] : entry.value;
      return `<div class="chart-tooltip-row"><span>${entry.marker}${entry.seriesName}</span><b>${formatNumber(Number(value))}</b></div>`;
    })
    .join("");
  return `<div class="chart-tooltip"><strong>${tooltipDate(dateValue)}</strong>${rows}</div>`;
}

function timeLine(
  name: string,
  data: Array<[string, number | null]>,
  color: string,
  extras: Partial<LineSeriesOption> = {},
): LineSeriesOption {
  return {
    name,
    type: "line",
    data,
    showSymbol: false,
    connectNulls: false,
    smooth: 0.24,
    lineStyle: { width: 2.5, color },
    itemStyle: { color },
    emphasis: { focus: "series" },
    ...extras,
  };
}

function withGapBreaks(points: WeightPoint[], selector: (point: WeightPoint) => number | null): Array<[string, number | null]> {
  const result: Array<[string, number | null]> = [];
  points.forEach((point, index) => {
    if (index > 0) {
      const previous = new Date(points[index - 1].measuredAt).getTime();
      const current = new Date(point.measuredAt).getTime();
      if (current - previous > 14 * 86_400_000) {
        result.push([new Date((previous + current) / 2).toISOString(), null]);
      }
    }
    result.push([point.measuredAt, selector(point)]);
  });
  return result;
}

export function weightChartOption(
  points: WeightPoint[],
  detailed = true,
  projection: WeightProjectionPoint[] = [],
  planProjection: WeightPlanPoint[] = [],
): EChartsOption {
  const normal = points.filter((point) => !point.isOutlier).map((point) => [point.measuredAt, point.weightKg]);
  const outliers = points.filter((point) => point.isOutlier).map((point) => [point.measuredAt, point.weightKg]);
  const series: EChartsOption["series"] = [
    {
      name: "Замеры",
      type: "scatter",
      data: normal,
      symbolSize: detailed ? 7 : 5,
      itemStyle: { color: colors.green, opacity: 0.58 },
      emphasis: { itemStyle: { opacity: 1 } },
    },
    timeLine("Тренд 7 дней", withGapBreaks(points, (point) => point.smoothed7dKg), colors.green, {
      lineStyle: { width: 3.5, color: colors.green },
      z: 5,
    }),
  ];
  const planLine = planProjection.length
    ? planProjection.map((point): [string, number] => [point.measuredAt, point.plannedKg])
    : points.map((point): [string, number | null] => [point.measuredAt, point.plannedKg]);
  if (planLine.some((point) => point[1] !== null)) {
    series.push(timeLine("План", planLine, colors.blue, {
      lineStyle: { width: 2, type: "dashed", color: colors.blue },
    }));
  }
  const forecastLine = projection.length
    ? projection.map((point): [string, number] => [point.measuredAt, point.forecastKg])
    : withGapBreaks(points, (point) => point.forecastKg);
  const forecastLow = projection.length
    ? projection.map((point): [string, number | null] => [point.measuredAt, point.forecastLowKg])
    : withGapBreaks(points, (point) => point.forecastLowKg);
  const forecastHigh = projection.length
    ? projection.map((point): [string, number | null] => [point.measuredAt, point.forecastHighKg])
    : withGapBreaks(points, (point) => point.forecastHighKg);
  if (detailed && forecastLine.some((point) => point[1] !== null)) {
    series.push(timeLine("Прогноз", forecastLine, colors.violet, {
      lineStyle: { width: 2.5, type: "dotted", color: colors.violet },
    }));
    series.push(timeLine("Диапазон прогноза", forecastLow, colors.violet, {
      lineStyle: { width: 1, type: "dashed", color: colors.violet, opacity: 0.45 },
      areaStyle: { color: "rgba(139,110,232,.06)" },
      silent: true,
    }));
    series.push(timeLine("Диапазон прогноза", forecastHigh, colors.violet, {
      lineStyle: { width: 1, type: "dashed", color: colors.violet, opacity: 0.45 },
      silent: true,
    }));
  }
  if (outliers.length) {
    series.push({
      name: "Необычный замер",
      type: "scatter",
      data: outliers,
      symbol: "diamond",
      symbolSize: 11,
      itemStyle: { color: colors.coral, borderColor: "#fff", borderWidth: 1 },
      z: 8,
    });
  }
  return {
    animationDuration: 550,
    color: [colors.green, colors.green, colors.blue, colors.violet, colors.coral],
    grid: sharedGrid,
    legend: { top: 6, left: 0, textStyle: { color: colors.muted }, itemWidth: 18, itemHeight: 8 },
    tooltip: { trigger: "axis", confine: true, formatter: tooltipFormatter, backgroundColor: "rgba(22,31,25,.95)", borderWidth: 0, textStyle: { color: "#fff" } },
    xAxis: { ...sharedAxis, type: "time", splitLine: { show: false }, axisLabel: { ...sharedAxis.axisLabel, formatter: (value: number) => formatShortDate(new Date(value).toISOString()) } },
    yAxis: { ...sharedAxis, type: "value", scale: true, name: "кг", nameTextStyle: { color: colors.muted } },
    dataZoom: detailed ? [{ type: "inside", filterMode: "none" }] : undefined,
    series,
  };
}

export function pressureChartOption(points: PressurePoint[]): EChartsOption {
  return {
    animationDuration: 500,
    color: [colors.coral, colors.blue, colors.amber],
    grid: sharedGrid,
    legend: { top: 6, left: 0, textStyle: { color: colors.muted }, itemWidth: 18, itemHeight: 8 },
    tooltip: { trigger: "axis", confine: true, formatter: tooltipFormatter, backgroundColor: "rgba(22,31,25,.95)", borderWidth: 0, textStyle: { color: "#fff" } },
    xAxis: { ...sharedAxis, type: "time", splitLine: { show: false }, axisLabel: { ...sharedAxis.axisLabel, formatter: (value: number) => formatShortDate(new Date(value).toISOString()) } },
    yAxis: [
      { ...sharedAxis, type: "value", scale: true, name: "мм рт. ст.", nameTextStyle: { color: colors.muted } },
      { ...sharedAxis, type: "value", scale: true, name: "уд/мин", nameTextStyle: { color: colors.muted }, splitLine: { show: false } },
    ],
    dataZoom: [{ type: "inside", filterMode: "none" }],
    series: [
      timeLine("Систолическое", points.map((point) => [point.measuredAt, point.systolic]), colors.coral),
      timeLine("Диастолическое", points.map((point) => [point.measuredAt, point.diastolic]), colors.blue),
      { ...timeLine("Пульс", points.map((point) => [point.measuredAt, point.pulse]), colors.amber), yAxisIndex: 1, lineStyle: { width: 1.8, color: colors.amber, opacity: 0.76 } },
    ],
  };
}

export function compositionChartOption(points: CompositionPoint[]): EChartsOption {
  return {
    animationDuration: 500,
    color: [colors.violet, colors.coral, colors.greenSoft],
    grid: sharedGrid,
    legend: { top: 6, left: 0, textStyle: { color: colors.muted }, itemWidth: 18, itemHeight: 8 },
    tooltip: { trigger: "axis", confine: true, formatter: tooltipFormatter, backgroundColor: "rgba(22,31,25,.95)", borderWidth: 0, textStyle: { color: "#fff" } },
    xAxis: { ...sharedAxis, type: "time", splitLine: { show: false }, axisLabel: { ...sharedAxis.axisLabel, formatter: (value: number) => formatShortDate(new Date(value).toISOString()) } },
    yAxis: [
      { ...sharedAxis, type: "value", scale: true, name: "кг", nameTextStyle: { color: colors.muted } },
      { ...sharedAxis, type: "value", scale: true, name: "%", nameTextStyle: { color: colors.muted }, splitLine: { show: false } },
    ],
    dataZoom: [{ type: "inside", filterMode: "none" }],
    series: [
      timeLine("Жировая масса", points.map((point) => [point.measuredAt, point.fatMassKg]), colors.coral),
      timeLine("Безжировая масса", points.map((point) => [point.measuredAt, point.leanMassKg]), colors.greenSoft),
      { ...timeLine("Доля жира", points.map((point) => [point.measuredAt, point.fatPct]), colors.violet), yAxisIndex: 1 },
    ],
  };
}
