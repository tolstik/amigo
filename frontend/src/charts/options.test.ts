import type { ActivityPoint, CircumferencePoint, HeartRateHourlyPoint, RecoveryPoint, WeeklyWeightPoint } from "../api/types";
import type { DailyPressureCategory } from "../lib/pressureCategories";
import { activityDailyChartOption, circumferenceChartOption, heartRateChartOption, pressureCategoryChartOption, pressureChartOption, recoveryChartOption, sleepChartOption, weeklyChangeChartOption, weeklyWeightChartOption } from "./options";
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
  it("plots independent waist and hip series in centimetres", () => {
    const points: CircumferencePoint[] = [
      { measuredOn: "2026-08-20", waistCm: 98.5, hipCm: null },
      { measuredOn: "2026-08-21", waistCm: null, hipCm: 108.2 },
    ];
    const option = circumferenceChartOption(points) as any;
    expect(option.yAxis.name).toBe("см");
    expect(option.series.map((item: any) => item.name)).toEqual(["Талия", "Бёдра"]);
    expect(option.series[0].data[1][1]).toBeNull();
    expect(option.series[1].data[0][1]).toBeNull();
  });

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

  it("plots sleep in hours and formats tooltips as hours and minutes", () => {
    const recovery: RecoveryPoint = {
      measuredAt: "2026-08-20",
      sleepMinutes: 461,
      deepSleepMinutes: 98,
      remSleepMinutes: 110,
      awakeMinutes: null,
      restingHeartRateBpm: null,
      averageHeartRateBpm: null,
      minimumHeartRateBpm: null,
      maximumHeartRateBpm: null,
      hrvRmssdMs: null,
      spo2Pct: null,
      vo2Max: null,
    };
    const option = sleepChartOption([recovery]);
    const series = option.series as any[];
    expect((option.yAxis as any).name).toBe("часы");
    expect(series[0].data[0][1]).toBeCloseTo(461 / 60);
    expect(series[1].data[0][1]).toBeCloseTo(98 / 60);
    const tooltip = (option.tooltip as any).formatter([{ axisValue: recovery.measuredAt, value: [recovery.measuredAt, 461 / 60], marker: "", seriesName: "Сон" }]);
    expect(tooltip).toContain("7 ч 41 мин");
  });
});

describe("daily pressure category strip", () => {
  it("adds labelled elevated and critical boundaries to the pressure trend", () => {
    const option = pressureChartOption([{
      measuredAt: "2026-08-25T09:00:00+03:00",
      systolic: 122,
      diastolic: 78,
      pulse: 64,
      pulsePressure: 44,
      sessionSize: 1,
      periodOfDay: "morning",
    }]);
    const series = option.series as any[];

    expect(series[0].markLine.data.map((line: any) => [line.name, line.yAxis])).toEqual([
      ["Сист. 135", 135],
      ["Сист. 180", 180],
    ]);
    expect(series[1].markLine.data.map((line: any) => [line.name, line.yAxis])).toEqual([
      ["Диаст. 85", 85],
      ["Диаст. 120", 120],
    ]);
  });

  it("uses one categorical bar per measured day and explains the category needing most attention", () => {
    const days: DailyPressureCategory[] = [{
      date: "2026-08-24",
      category: "home_guide",
      sessions: 1,
      minSystolic: 122,
      maxSystolic: 122,
      minDiastolic: 78,
      maxDiastolic: 78,
    }, {
      date: "2026-08-25",
      category: "critical_high",
      sessions: 3,
      minSystolic: 88,
      maxSystolic: 181,
      minDiastolic: 58,
      maxDiastolic: 86,
    }];
    const option = pressureCategoryChartOption(days);
    const series = option.series as any[];

    expect((option.xAxis as any).type).toBe("category");
    expect((option.xAxis as any).data).toEqual(["2026-08-24", "2026-08-25"]);
    expect(series[0].type).toBe("bar");
    expect(series[0].data.map((item: any) => item.itemStyle.color)).toEqual(["#2d9365", "#e9785d"]);
    const tooltip = (option.tooltip as any).formatter({ dataIndex: 1 });
    expect(tooltip).toContain("Критически высокое");
    expect(tooltip).toContain("сист. ≥ 180 или диаст. ≥ 120");
    expect(tooltip).toContain("88–181 / 58–86");
    expect(tooltip).toContain("Сессий");
  });
});
