import type { ActivityPoint, HeartRateHourlyPoint, RecoveryPoint, WeeklyWeightPoint } from "../api/types";
import { activityDailyChartOption, heartRateChartOption, recoveryChartOption, sleepChartOption, weeklyChangeChartOption, weeklyWeightChartOption } from "./options";
import { chartOptionForTheme, chartPalettes } from "./theme";

function week(index: number, overrides: Partial<WeeklyWeightPoint> = {}): WeeklyWeightPoint {
  const start = new Date(Date.UTC(2026, 7, 17 + index * 7));
  const end = new Date(start.getTime() + 6 * 86_400_000);
  return {
    startDate: start.toISOString().slice(0, 10),
    endDate: end.toISOString().slice(0, 10),
    actualAvgKg: 126 - index * 0.4,
    actualMinKg: 125.8 - index * 0.4,
    plannedAvgKg: 126.2 - index * 0.5,
    actualChangeKg: index === 0 ? null : -0.4,
    plannedChangeKg: index === 0 ? null : -0.5,
    deviationFromPlanKg: -0.2 + index * 0.1,
    measurementDays: 3,
    sampleCount: 4,
    outlierDays: 0,
    isPartial: false,
    ...overrides,
  };
}

describe("weekly chart options", () => {
  it("renders paired plan/fact bars and preserves a gap in the minimum line", () => {
    const points = [week(0, { isPartial: true }), week(1, { actualAvgKg: null, actualMinKg: null, measurementDays: 0, sampleCount: 0 })];
    const option = weeklyWeightChartOption(points);
    const series = option.series as any[];

    expect(series.map((item) => [item.name, item.type])).toEqual([
      ["Факт", "bar"],
      ["План", "bar"],
      ["Минимум", "line"],
    ]);
    expect(series[0].data).toEqual([126, null]);
    expect(series[2].data).toEqual([125.8, null]);
    expect(series[2].connectNulls).toBe(false);

    const tooltip = (option.tooltip as any).formatter([
      { axisValue: points[0].startDate, value: 126, marker: "", seriesName: "Факт" },
    ]);
    expect(tooltip).toContain("неполная неделя");
    expect(tooltip).toContain("Отклонение факт − план");
    expect(tooltip).toContain("Дней с замерами");
    expect(tooltip).toContain("Всего замеров");
  });

  it("keeps negative changes as weight loss and opens long histories on recent weeks", () => {
    const points = Array.from({ length: 14 }, (_, index) => week(index));
    points[2] = week(2, { actualChangeKg: -0.7 });
    points[3] = week(3, { actualChangeKg: 0.1 });
    const option = weeklyChangeChartOption(points);
    const series = option.series as any[];
    const zoom = option.dataZoom as any[];
    const factColor = series[0].itemStyle.color;

    expect(series[0].data[1]).toBe(-0.4);
    expect(series[1].data[1]).toBe(-0.5);
    expect(factColor({ value: -0.4, dataIndex: 1 })).toBe("#d99b35");
    expect(factColor({ value: -0.7, dataIndex: 2 })).toBe("#2d9365");
    expect(factColor({ value: 0.1, dataIndex: 3 })).toBe("#e9785d");
    expect(zoom.map((item) => item.type)).toEqual(["inside", "slider"]);
    expect(zoom[0]).toMatchObject({ startValue: 2, endValue: 13 });
  });

  it("recolors static chart elements and callback colors for the selected theme", () => {
    const option = chartOptionForTheme(weeklyChangeChartOption([week(0), week(1)]), "ocean");
    const series = option.series as any[];
    const factColor = series[0].itemStyle.color;

    expect((option.legend as any).textStyle.color).toBe(chartPalettes.ocean.muted);
    expect((option.yAxis as any).axisLine.lineStyle.color).toBe(chartPalettes.ocean.grid);
    expect(series[1].itemStyle.color).toBe(chartPalettes.ocean.blue);
    expect(factColor({ value: -0.7, dataIndex: 1 })).toBe(chartPalettes.ocean.green);
    expect(factColor({ value: 0.1, dataIndex: 1 })).toBe(chartPalettes.ocean.coral);
  });
});

describe("watch heart-rate chart", () => {
  it("renders daily minimum, average and maximum without treating them as resting heart rate", () => {
    const point: HeartRateHourlyPoint = {
      measuredAt: "2026-08-20T12:00:00+03:00",
      averageBpm: 59,
      minimumBpm: 47,
      maximumBpm: 89,
      sampleCount: 12,
    };

    const series = heartRateChartOption([point]).series as any[];

    expect(series.map((item) => item.name)).toEqual(["Минимум", "Средний", "Максимум"]);
    expect(series.map((item) => item.data[0][1])).toEqual([47, 59, 89]);
    expect((heartRateChartOption([point]).xAxis as any).type).toBe("time");
  });
});

describe("daily charts", () => {
  it("uses date categories for activity, sleep, resting heart rate and HRV", () => {
    const activity: ActivityPoint = {
      measuredAt: "2026-08-20",
      steps: 8_000,
      distanceKm: 5.5,
      activeCaloriesKcal: 400,
      totalCaloriesKcal: 2_200,
      activeMinutes: 55,
      workoutMinutes: 30,
      workouts: 1,
    };
    const recovery: RecoveryPoint = {
      measuredAt: "2026-08-20",
      sleepMinutes: 420,
      deepSleepMinutes: 90,
      remSleepMinutes: 80,
      awakeMinutes: 30,
      restingHeartRateBpm: 58,
      averageHeartRateBpm: 65,
      minimumHeartRateBpm: 48,
      maximumHeartRateBpm: 90,
      hrvRmssdMs: 42,
      spo2Pct: 97,
      vo2Max: 35,
    };

    for (const option of [
      activityDailyChartOption([activity]),
      sleepChartOption([recovery]),
      recoveryChartOption([recovery]),
    ]) {
      expect((option.xAxis as any).type).toBe("category");
      expect((option.xAxis as any).data).toEqual(["2026-08-20"]);
    }
  });
});
