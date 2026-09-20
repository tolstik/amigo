import { describe, expect, it } from "vitest";
import type { WeightPoint } from "../api/types";
import { bmiChartOption, dailyWeightDerived, monthBoundaryLines, planDeviationChartOption, weightChartOption } from "./options";

const point = (date: string, weightKg: number, plannedKg: number | null, isOutlier = false) => ({ measuredAt: `${date}T00:00:00+03:00`, weightKg, plannedKg, isOutlier, smoothed7dKg: weightKg, forecastKg: null, forecastLowKg: null, forecastHighKg: null }) as WeightPoint;

describe("daily progress", () => {
  it("compares actual weight and plan on the same date and keeps BMI numeric without classification", () => {
    const rows = dailyWeightDerived([point("2026-09-01", 120, 121), point("2026-09-03", 121, 120), point("2026-09-04", 180, 120, true), point("2026-08-01", 127, null)], 176);
    expect(rows.map((row) => row.deviationKg)).toEqual([-1, 1, null, null]);
    expect(rows[0].bmi).toBe(38.74);
    expect(dailyWeightDerived([point("2026-09-01", 120, 121)], 0)[0].bmi).toBeNull();
  });
  it("breaks unmeasured days for both derived charts and preserves a visible zero reference", () => {
    const rows = [point("2026-09-01", 120, 121), point("2026-09-03", 121, 120)];
    for (const option of [bmiChartOption(rows, 176), planDeviationChartOption(rows)] as any[]) {
      expect(option.series[0].data[1][1]).toBeNull();
      expect(option.series[0].connectNulls).toBe(false);
      expect(option.series[0].smooth).toBe(false);
    }
    expect((planDeviationChartOption(rows).series as any[])[0].markLine.data).toEqual([{ yAxis: 0 }]);
  });
  it("places month dividers at Moscow midnight including a year boundary", () => {
    const dates = ["2026-11-25T09:00:00Z", "2027-01-05T09:00:00Z"];
    expect((monthBoundaryLines(dates) as any).data.map((item: any) => new Date(item.xAxis).toISOString())).toEqual(["2026-11-30T21:00:00.000Z", "2026-12-31T21:00:00.000Z"]);
    expect((monthBoundaryLines([]) as any).data).toEqual([]);
    const rows = [point("2026-08-20", 127, 126), point("2026-09-04", 123, 124)];
    expect((weightChartOption(rows, false, [], [], true).series as any[])[1].markLine.data).toHaveLength(1);
  });
});
