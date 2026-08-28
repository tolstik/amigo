import type { BarSeriesOption, EChartsOption, LineSeriesOption } from "echarts";
import type {
  ActivityPoint,
  CompositionPoint,
  CircumferencePoint,
  HeartRateHourlyPoint,
  LabResult,
  PressurePoint,
  RecoveryPoint,
  WeeklyActivityPoint,
  WeeklyWeightPoint,
  WeightPlanPoint,
  WeightPoint,
  WeightProjectionPoint,
} from "../api/types";
import { formatDate, formatDateTime, formatDelta, formatKg, formatNumber, formatShortDate } from "../lib/format";
import { heartRateLineData, type HeartRateAggregationHours } from "./heartRate";
import type { DailyPressureCategory, PressureCategory } from "../lib/pressureCategories";
import { PRESSURE_CATEGORY_DEFINITIONS } from "../lib/pressureCategories";

const colors = {
  green: "#2d9365",
  greenSoft: "#77c69d",
  blue: "#4b7bec",
  violet: "#8b6ee8",
  coral: "#e9785d",
  amber: "#d99b35",
  muted: "#6d796f",
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

function sleepHours(value: number | null): number | null {
  return value === null ? null : value / 60;
}

function sleepDurationFromHours(value: number): string {
  const totalMinutes = Math.max(0, Math.round(value * 60));
  return `${Math.floor(totalMinutes / 60)} ч ${totalMinutes % 60} мин`;
}

function sleepTooltipFormatter(params: any): string {
  const entries = Array.isArray(params) ? params : [params];
  if (!entries.length) return "";
  const dateValue = entries[0]?.axisValue ?? entries[0]?.value?.[0];
  const rows = entries
    .filter((entry: any) => {
      const value = Array.isArray(entry.value) ? entry.value[1] : entry.value;
      return typeof value === "number" && Number.isFinite(value);
    })
    .map((entry: any) => {
      const value = Array.isArray(entry.value) ? entry.value[1] : entry.value;
      return `<div class="chart-tooltip-row"><span>${entry.marker}${entry.seriesName}</span><b>${sleepDurationFromHours(Number(value))}</b></div>`;
    })
    .join("");
  return `<div class="chart-tooltip"><strong>${tooltipDate(dateValue)}</strong>${rows}</div>`;
}

function heartRateTooltipFormatter(params: any): string {
  const entries = Array.isArray(params) ? params : [params];
  if (!entries.length) return "";
  const dateValue = entries[0]?.axisValue ?? entries[0]?.value?.[0];
  const parsedDate = new Date(dateValue);
  const title = Number.isNaN(parsedDate.getTime()) ? "" : formatDateTime(parsedDate.toISOString());
  const rows = entries
    .filter((entry: any) => {
      const value = Array.isArray(entry.value) ? entry.value[1] : entry.value;
      return typeof value === "number" && Number.isFinite(value);
    })
    .map((entry: any) => {
      const value = Array.isArray(entry.value) ? entry.value[1] : entry.value;
      return `<div class="chart-tooltip-row"><span>${entry.marker}${entry.seriesName}</span><b>${formatNumber(Number(value))} уд/мин</b></div>`;
    })
    .join("");
  return `<div class="chart-tooltip"><strong>${title}</strong>${rows}</div>`;
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

function weeklyTooltipFormatter(
  points: WeeklyWeightPoint[],
  valueFormatter: (value: number) => string,
  comparison: {
    label: string;
    value: (point: WeeklyWeightPoint) => number | null;
  },
) {
  const byStartDate = new Map(points.map((point) => [point.startDate, point]));
  return (params: any): string => {
    const entries = Array.isArray(params) ? params : [params];
    const point = byStartDate.get(String(entries[0]?.axisValue ?? ""));
    if (!point) return "";
    const rows = entries
      .filter((entry: any) => {
        const value = Array.isArray(entry.value) ? entry.value.at(-1) : entry.value;
        return typeof value === "number" && Number.isFinite(value);
      })
      .map((entry: any) => {
        const value = Array.isArray(entry.value) ? entry.value.at(-1) : entry.value;
        return `<div class="chart-tooltip-row"><span>${entry.marker}${entry.seriesName}</span><b>${valueFormatter(Number(value))}</b></div>`;
      })
      .join("");
    const comparisonValue = comparison.value(point);
    const comparisonRow = comparisonValue === null
      ? ""
      : `<div class="chart-tooltip-row"><span>${comparison.label}</span><b>${formatDelta(comparisonValue)}</b></div>`;
    const coverage = `<div class="chart-tooltip-row"><span>Дней с замерами</span><b>${point.measurementDays}</b></div><div class="chart-tooltip-row"><span>Всего замеров</span><b>${point.sampleCount}</b></div>`;
    const outliers = point.outlierDays
      ? `<div class="chart-tooltip-row"><span>Дней-выбросов</span><b>${point.outlierDays}</b></div>`
      : "";
    const partial = point.isPartial ? " · неполная неделя" : "";
    return `<div class="chart-tooltip"><strong>${formatDate(point.startDate)} — ${formatDate(point.endDate)}${partial}</strong>${rows}${comparisonRow}${coverage}${outliers}</div>`;
  };
}

function weeklyDataZoom(points: WeeklyWeightPoint[]): EChartsOption["dataZoom"] {
  const longHistory = points.length > 12;
  const common = longHistory
    ? { startValue: Math.max(0, points.length - 12), endValue: points.length - 1 }
    : {};
  return [
    { type: "inside", xAxisIndex: 0, filterMode: "none", ...common },
    ...(longHistory
      ? [{ type: "slider" as const, xAxisIndex: 0, filterMode: "none" as const, height: 18, bottom: 8, showDetail: false, ...common }]
      : []),
  ];
}

function weeklyCategoryAxis(points: WeeklyWeightPoint[]) {
  return {
    ...sharedAxis,
    type: "category" as const,
    data: points.map((point) => point.startDate),
    boundaryGap: true,
    splitLine: { show: false },
    axisLabel: {
      ...sharedAxis.axisLabel,
      formatter: (value: string) => formatDate(value, false),
    },
  };
}

function dailyCategoryAxis(points: Array<{ measuredAt: string }>) {
  return {
    ...sharedAxis,
    type: "category" as const,
    data: points.map((point) => point.measuredAt),
    boundaryGap: true,
    splitLine: { show: false },
    axisLabel: {
      ...sharedAxis.axisLabel,
      formatter: (value: string) => formatDate(value, false),
    },
  };
}

function weeklyBar(name: string, data: Array<number | null>, color: string): BarSeriesOption {
  return {
    name,
    type: "bar",
    data,
    barMaxWidth: 30,
    itemStyle: { color, borderRadius: [5, 5, 1, 1] },
    emphasis: { focus: "series" },
  };
}

export function weeklyWeightChartOption(points: WeeklyWeightPoint[]): EChartsOption {
  const longHistory = points.length > 12;
  return {
    animationDuration: 500,
    color: [colors.green, colors.blue, colors.amber],
    grid: { ...sharedGrid, bottom: longHistory ? 76 : sharedGrid.bottom },
    legend: { top: 6, left: 0, textStyle: { color: colors.muted }, itemWidth: 18, itemHeight: 8 },
    tooltip: {
      trigger: "axis",
      axisPointer: { type: "shadow" },
      confine: true,
      formatter: weeklyTooltipFormatter(points, formatKg, {
        label: "Отклонение факт − план",
        value: (point) => point.deviationFromPlanKg,
      }),
      backgroundColor: "rgba(22,31,25,.95)",
      borderWidth: 0,
      textStyle: { color: "#fff" },
    },
    xAxis: weeklyCategoryAxis(points),
    yAxis: { ...sharedAxis, type: "value", scale: true, name: "кг", nameTextStyle: { color: colors.muted } },
    dataZoom: weeklyDataZoom(points),
    series: [
      weeklyBar("Факт", points.map((point) => point.actualAvgKg), colors.green),
      { ...weeklyBar("План", points.map((point) => point.plannedAvgKg), colors.blue), itemStyle: { color: colors.blue, opacity: 0.72, borderRadius: [5, 5, 1, 1] } },
      {
        name: "Минимум",
        type: "line",
        data: points.map((point) => point.actualMinKg),
        connectNulls: false,
        showSymbol: true,
        symbolSize: 7,
        lineStyle: { width: 2.5, color: colors.amber },
        itemStyle: { color: colors.amber, borderColor: "#fff", borderWidth: 1 },
        emphasis: { focus: "series" },
        z: 5,
      },
    ],
  };
}

export function weeklyChangeChartOption(points: WeeklyWeightPoint[]): EChartsOption {
  const longHistory = points.length > 12;
  const fact = weeklyBar("Факт", points.map((point) => point.actualChangeKg), colors.green);
  fact.itemStyle = {
    borderRadius: [4, 4, 4, 4],
    color: (params: any) => {
      const value = Number(params.value);
      const planned = points[Number(params.dataIndex)]?.plannedChangeKg;
      if (planned !== null && planned !== undefined && value <= planned) return colors.green;
      return value < 0 ? colors.amber : colors.coral;
    },
  };
  return {
    animationDuration: 500,
    color: [colors.green, colors.blue],
    grid: { ...sharedGrid, bottom: longHistory ? 76 : sharedGrid.bottom },
    legend: { top: 6, left: 0, textStyle: { color: colors.muted }, itemWidth: 18, itemHeight: 8 },
    tooltip: {
      trigger: "axis",
      axisPointer: { type: "shadow" },
      confine: true,
      formatter: weeklyTooltipFormatter(points, formatDelta, {
        label: "Разница темпа факт − план",
        value: (point) => point.actualChangeKg !== null && point.plannedChangeKg !== null
          ? point.actualChangeKg - point.plannedChangeKg
          : null,
      }),
      backgroundColor: "rgba(22,31,25,.95)",
      borderWidth: 0,
      textStyle: { color: "#fff" },
    },
    xAxis: weeklyCategoryAxis(points),
    yAxis: {
      ...sharedAxis,
      type: "value",
      name: "кг · снижение ниже 0",
      nameTextStyle: { color: colors.muted },
      axisLabel: { ...sharedAxis.axisLabel, formatter: (value: number) => formatNumber(value) },
    },
    dataZoom: weeklyDataZoom(points),
    series: [
      fact,
      { ...weeklyBar("План", points.map((point) => point.plannedChangeKg), colors.blue), itemStyle: { color: colors.blue, opacity: 0.72, borderRadius: [4, 4, 4, 4] } },
    ],
  };
}

export function activityDailyChartOption(points: ActivityPoint[]): EChartsOption {
  return {
    animationDuration: 500,
    color: [colors.green, colors.blue],
    grid: sharedGrid,
    legend: { top: 6, left: 0, textStyle: { color: colors.muted }, itemWidth: 18, itemHeight: 8 },
    tooltip: {
      trigger: "axis",
      confine: true,
      formatter: tooltipFormatter,
      backgroundColor: "rgba(22,31,25,.95)",
      borderWidth: 0,
      textStyle: { color: "#fff" },
    },
    xAxis: dailyCategoryAxis(points),
    yAxis: [
      { ...sharedAxis, type: "value", name: "шаги", nameTextStyle: { color: colors.muted }, min: 0 },
      { ...sharedAxis, type: "value", name: "мин", nameTextStyle: { color: colors.muted }, min: 0, splitLine: { show: false } },
    ],
    dataZoom: [{ type: "inside", filterMode: "none" }],
    series: [
      {
        name: "Шаги",
        type: "bar",
        data: points.map((point) => [point.measuredAt, point.steps]),
        barMaxWidth: 16,
        itemStyle: { color: colors.green, borderRadius: [4, 4, 1, 1] },
      },
      timeLine("Активные минуты", points.map((point) => [point.measuredAt, point.activeMinutes]), colors.blue, { yAxisIndex: 1 }),
    ],
  };
}

export function weeklyActivityChartOption(points: WeeklyActivityPoint[]): EChartsOption {
  const axis = points.map((point) => point.startDate);
  return {
    animationDuration: 500,
    color: [colors.green, colors.blue],
    grid: sharedGrid,
    legend: { top: 6, left: 0, textStyle: { color: colors.muted }, itemWidth: 18, itemHeight: 8 },
    tooltip: {
      trigger: "axis",
      axisPointer: { type: "shadow" },
      confine: true,
      backgroundColor: "rgba(22,31,25,.95)",
      borderWidth: 0,
      textStyle: { color: "#fff" },
    },
    xAxis: {
      ...sharedAxis,
      type: "category",
      data: axis,
      axisLabel: { ...sharedAxis.axisLabel, formatter: (value: string) => formatDate(value, false) },
      splitLine: { show: false },
    },
    yAxis: { ...sharedAxis, type: "value", min: 0, name: "шаги за неделю", nameTextStyle: { color: colors.muted } },
    dataZoom: [{ type: "inside", filterMode: "none" }],
    series: [
      weeklyBar("Факт", points.map((point) => point.actualSteps), colors.green),
      { ...weeklyBar("Личная база", points.map((point) => point.baselineSteps), colors.blue), itemStyle: { color: colors.blue, opacity: .72, borderRadius: [5, 5, 1, 1] } },
    ],
  };
}

export function sleepChartOption(points: RecoveryPoint[]): EChartsOption {
  return {
    animationDuration: 500,
    color: [colors.violet, colors.blue, colors.green],
    grid: sharedGrid,
    legend: { top: 6, left: 0, textStyle: { color: colors.muted }, itemWidth: 18, itemHeight: 8 },
    tooltip: {
      trigger: "axis",
      confine: true,
      formatter: sleepTooltipFormatter,
      backgroundColor: "rgba(22,31,25,.95)",
      borderWidth: 0,
      textStyle: { color: "#fff" },
    },
    xAxis: dailyCategoryAxis(points),
    yAxis: {
      ...sharedAxis,
      type: "value",
      min: 0,
      name: "часы",
      nameTextStyle: { color: colors.muted },
      axisLabel: { ...sharedAxis.axisLabel, formatter: (value: number) => formatNumber(value, 1) },
    },
    dataZoom: [{ type: "inside", filterMode: "none" }],
    series: [
      {
        name: "Сон",
        type: "bar",
        data: points.map((point) => [point.measuredAt, sleepHours(point.sleepMinutes)]),
        barMaxWidth: 18,
        itemStyle: { color: colors.violet, borderRadius: [4, 4, 1, 1] },
      },
      timeLine("Глубокий сон", points.map((point) => [point.measuredAt, sleepHours(point.deepSleepMinutes)]), colors.blue),
      timeLine("REM", points.map((point) => [point.measuredAt, sleepHours(point.remSleepMinutes)]), colors.green),
    ],
  };
}

export function recoveryChartOption(points: RecoveryPoint[]): EChartsOption {
  return {
    animationDuration: 500,
    color: [colors.coral, colors.green],
    grid: sharedGrid,
    legend: { top: 6, left: 0, textStyle: { color: colors.muted }, itemWidth: 18, itemHeight: 8 },
    tooltip: {
      trigger: "axis",
      confine: true,
      formatter: tooltipFormatter,
      backgroundColor: "rgba(22,31,25,.95)",
      borderWidth: 0,
      textStyle: { color: "#fff" },
    },
    xAxis: dailyCategoryAxis(points),
    yAxis: [
      { ...sharedAxis, type: "value", scale: true, name: "пульс", nameTextStyle: { color: colors.muted } },
      { ...sharedAxis, type: "value", scale: true, name: "HRV, мс", nameTextStyle: { color: colors.muted }, splitLine: { show: false } },
    ],
    dataZoom: [{ type: "inside", filterMode: "none" }],
    series: [
      timeLine("Пульс покоя", points.map((point) => [point.measuredAt, point.restingHeartRateBpm]), colors.coral),
      timeLine("HRV", points.map((point) => [point.measuredAt, point.hrvRmssdMs]), colors.green, { yAxisIndex: 1 }),
    ],
  };
}

export function heartRateChartOption(
  points: HeartRateHourlyPoint[],
  aggregationHours: HeartRateAggregationHours = 1,
): EChartsOption {
  const ordered = [...points].sort((left, right) => Date.parse(left.measuredAt) - Date.parse(right.measuredAt));
  const visibleStart = ordered.length > 160 ? ordered.at(-160)?.measuredAt : undefined;
  const zoomRange = visibleStart
    ? { startValue: visibleStart, endValue: ordered.at(-1)?.measuredAt }
    : {};
  return {
    animationDuration: 500,
    color: [colors.blue, colors.coral, colors.violet],
    grid: { ...sharedGrid, bottom: 78 },
    legend: { top: 6, left: 0, textStyle: { color: colors.muted }, itemWidth: 18, itemHeight: 8 },
    tooltip: {
      trigger: "axis",
      confine: true,
      formatter: heartRateTooltipFormatter,
      backgroundColor: "rgba(22,31,25,.95)",
      borderWidth: 0,
      textStyle: { color: "#fff" },
    },
    xAxis: {
      ...sharedAxis,
      type: "time",
      splitLine: { show: false },
      axisLabel: {
        ...sharedAxis.axisLabel,
        formatter: (value: number) => aggregationHours === 24
          ? formatShortDate(new Date(value).toISOString())
          : formatDateTime(new Date(value).toISOString()),
      },
    },
    yAxis: { ...sharedAxis, type: "value", scale: true, name: "уд/мин", nameTextStyle: { color: colors.muted } },
    dataZoom: [
      { type: "inside", xAxisIndex: 0, filterMode: "none", ...zoomRange },
      {
        type: "slider",
        xAxisIndex: 0,
        filterMode: "none",
        height: 20,
        bottom: 8,
        showDataShadow: false,
        showDetail: true,
        brushSelect: false,
        borderColor: colors.grid,
        backgroundColor: "transparent",
        fillerColor: "rgba(75,123,236,.12)",
        handleStyle: { color: colors.blue, borderColor: colors.blue },
        moveHandleStyle: { color: colors.blue, opacity: 0.65 },
        textStyle: { color: colors.muted },
        ...zoomRange,
      },
    ],
    series: [
      timeLine("Минимум", heartRateLineData(ordered, (point) => point.minimumBpm, aggregationHours), colors.blue, {
        smooth: false,
        lineStyle: { width: 1.5, type: "dashed", color: colors.blue, opacity: 0.72 },
      }),
      timeLine("Средний", heartRateLineData(ordered, (point) => point.averageBpm, aggregationHours), colors.coral, {
        smooth: false,
        lineStyle: { width: 3, color: colors.coral },
        z: 4,
      }),
      timeLine("Максимум", heartRateLineData(ordered, (point) => point.maximumBpm, aggregationHours), colors.violet, {
        smooth: false,
        lineStyle: { width: 1.5, type: "dashed", color: colors.violet, opacity: 0.72 },
      }),
    ],
  };
}

export function labHistoryChartOption(rows: LabResult[], unit: string): EChartsOption {
  const numeric = rows.filter((row) => row.value_numeric !== null);
  const axis = numeric.map((row, index) => `${row.observed_on ?? "Без даты"}#${index}`);
  return {
    animationDuration: 450,
    color: [colors.green, colors.blue, colors.violet],
    grid: { ...sharedGrid, top: 48 },
    legend: { top: 4, left: 0, textStyle: { color: colors.muted } },
    tooltip: {
      trigger: "axis",
      confine: true,
      formatter: (params: any) => {
        const entries = Array.isArray(params) ? params : [params];
        const index = Number(entries[0]?.dataIndex ?? 0);
        const row = numeric[index];
        if (!row) return "";
        const values = entries
          .filter((entry: any) => entry.value !== null && entry.value !== undefined)
          .map((entry: any) => `<div class="chart-tooltip-row"><span>${entry.marker}${entry.seriesName}</span><b>${formatNumber(Number(entry.value))}</b></div>`)
          .join("");
        return `<div class="chart-tooltip"><strong>${formatDate(row.observed_on)}</strong>${values}</div>`;
      },
      backgroundColor: "rgba(22,31,25,.95)",
      borderWidth: 0,
      textStyle: { color: "#fff" },
    },
    xAxis: {
      ...sharedAxis,
      type: "category",
      data: axis,
      boundaryGap: numeric.length < 2,
      splitLine: { show: false },
      axisLabel: {
        ...sharedAxis.axisLabel,
        formatter: (value: string) => formatDate(value.split("#", 1)[0], false),
      },
    },
    yAxis: { ...sharedAxis, type: "value", scale: true, name: unit, nameTextStyle: { color: colors.muted } },
    dataZoom: numeric.length > 12 ? [{ type: "inside", filterMode: "none" }] : undefined,
    series: [
      {
        name: "Значение",
        type: "line",
        data: numeric.map((row) => row.value_numeric),
        showSymbol: true,
        symbolSize: 8,
        smooth: false,
        connectNulls: false,
        lineStyle: { width: 3, color: colors.green },
        itemStyle: { color: colors.green, borderColor: "#fff", borderWidth: 1 },
      },
      {
        name: "Нижняя граница",
        type: "line",
        data: numeric.map((row) => row.reference_low),
        showSymbol: false,
        connectNulls: false,
        lineStyle: { width: 1.5, type: "dashed", color: colors.blue },
      },
      {
        name: "Верхняя граница",
        type: "line",
        data: numeric.map((row) => row.reference_high),
        showSymbol: false,
        connectNulls: false,
        lineStyle: { width: 1.5, type: "dashed", color: colors.violet },
      },
    ],
  };
}

export function circumferenceChartOption(points: CircumferencePoint[]): EChartsOption {
  return {
    animationDuration: 500,
    color: [colors.coral, colors.violet],
    grid: sharedGrid,
    legend: { top: 6, left: 0, textStyle: { color: colors.muted }, itemWidth: 18, itemHeight: 8 },
    tooltip: { trigger: "axis", confine: true, formatter: tooltipFormatter, backgroundColor: "rgba(22,31,25,.95)", borderWidth: 0, textStyle: { color: "#fff" } },
    xAxis: { ...sharedAxis, type: "time", splitLine: { show: false }, axisLabel: { ...sharedAxis.axisLabel, formatter: (value: number) => formatShortDate(new Date(value).toISOString()) } },
    yAxis: { ...sharedAxis, type: "value", scale: true, name: "см", nameTextStyle: { color: colors.muted } },
    dataZoom: [{ type: "inside", filterMode: "none" }],
    series: [
      timeLine("Талия", points.map((point) => [point.measuredOn, point.waistCm]), colors.coral),
      timeLine("Бёдра", points.map((point) => [point.measuredOn, point.hipCm]), colors.violet),
    ],
  };
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
  const elevatedGuideLine = (name: string, value: number) => ({
    name,
    yAxis: value,
    lineStyle: { color: colors.amber, type: "dashed" as const, width: 1.2, opacity: 0.78 },
    label: { formatter: name, position: "insideEndTop" as const, color: colors.muted, fontSize: 9 },
  });
  const criticalGuideLine = (name: string, value: number) => ({
    name,
    yAxis: value,
    lineStyle: { color: colors.coral, type: "dashed" as const, width: 1.2, opacity: 0.82 },
    label: { formatter: name, position: "insideEndTop" as const, color: colors.muted, fontSize: 9 },
  });
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
      {
        ...timeLine("Систолическое", points.map((point) => [point.measuredAt, point.systolic]), colors.coral),
        markLine: {
          silent: true,
          symbol: "none",
          data: [
            elevatedGuideLine("Сист. 135", 135),
            criticalGuideLine("Сист. 180", 180),
          ],
        },
      },
      {
        ...timeLine("Диастолическое", points.map((point) => [point.measuredAt, point.diastolic]), colors.blue),
        markLine: {
          silent: true,
          symbol: "none",
          data: [
            elevatedGuideLine("Диаст. 85", 85),
            criticalGuideLine("Диаст. 120", 120),
          ],
        },
      },
      { ...timeLine("Пульс", points.map((point) => [point.measuredAt, point.pulse]), colors.amber), yAxisIndex: 1, lineStyle: { width: 1.8, color: colors.amber, opacity: 0.76 } },
    ],
  };
}

const pressureCategoryColors: Record<PressureCategory, string> = {
  below_guide: colors.muted,
  home_guide: colors.green,
  elevated: colors.amber,
  critical_high: colors.coral,
};

function pressureCategoryTooltip(days: DailyPressureCategory[]) {
  return (params: any): string => {
    const entry = Array.isArray(params) ? params[0] : params;
    const day = days[Number(entry?.dataIndex)];
    if (!day) return "";
    const definition = PRESSURE_CATEGORY_DEFINITIONS[day.category];
    const boundary = definition.boundary.replaceAll("<", "&lt;");
    const systolic = day.minSystolic === day.maxSystolic
      ? formatNumber(day.minSystolic, 0)
      : `${formatNumber(day.minSystolic, 0)}–${formatNumber(day.maxSystolic, 0)}`;
    const diastolic = day.minDiastolic === day.maxDiastolic
      ? formatNumber(day.minDiastolic, 0)
      : `${formatNumber(day.minDiastolic, 0)}–${formatNumber(day.maxDiastolic, 0)}`;
    return `<div class="chart-tooltip"><strong>${formatDate(day.date)}</strong>` +
      `<div class="chart-tooltip-row"><span>Категория дня</span><b>${definition.label}</b></div>` +
      `<div class="chart-tooltip-row"><span>Границы</span><b>${boundary}</b></div>` +
      `<div class="chart-tooltip-row"><span>Диапазон сессий</span><b>${systolic} / ${diastolic}</b></div>` +
      `<div class="chart-tooltip-row"><span>Сессий</span><b>${day.sessions}</b></div></div>`;
  };
}

export function pressureCategoryChartOption(days: DailyPressureCategory[]): EChartsOption {
  const longHistory = days.length > 60;
  return {
    animationDuration: 350,
    grid: {
      left: 12,
      right: 12,
      top: 8,
      bottom: longHistory ? 62 : 40,
      containLabel: true,
    },
    tooltip: {
      trigger: "item",
      confine: true,
      formatter: pressureCategoryTooltip(days),
      backgroundColor: "rgba(22,31,25,.95)",
      borderWidth: 0,
      textStyle: { color: "#fff" },
    },
    xAxis: {
      ...sharedAxis,
      type: "category",
      data: days.map((day) => day.date),
      splitLine: { show: false },
      axisLabel: {
        ...sharedAxis.axisLabel,
        formatter: (value: string) => formatShortDate(value),
      },
    },
    yAxis: {
      type: "value",
      min: 0,
      max: 1,
      show: false,
    },
    dataZoom: longHistory ? [
      { type: "inside", filterMode: "none", startValue: Math.max(0, days.length - 60), endValue: days.length - 1 },
      { type: "slider", filterMode: "none", startValue: Math.max(0, days.length - 60), endValue: days.length - 1, height: 16, bottom: 5 },
    ] : undefined,
    series: [{
      name: "Категория дня",
      type: "bar",
      data: days.map((day) => ({
        name: day.date,
        value: 1,
        itemStyle: { color: pressureCategoryColors[day.category], borderRadius: 4 },
      })),
      barWidth: "88%",
      barMaxWidth: 72,
      barMinHeight: 24,
      emphasis: { itemStyle: { opacity: 0.82 } },
    }],
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
