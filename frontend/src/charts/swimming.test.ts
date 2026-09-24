import { describe, expect, it } from "vitest";
import { swimmingChartOption, swimmingIntensityChartOption } from "./options";
import { chartOptionForTheme } from "./theme";
import type { ThemeName } from "../theme/ThemeProvider";

describe("pool charts", () => {
  it("keeps missing distances empty and converts only chart duration to minutes", () => {
    const points = [
      { start_time: "2026-09-01T09:00:00Z", distance_meters: 1000, duration_seconds: 1800, kilocalories: 240, average_bpm: 126 },
      { start_time: "2026-09-02T09:00:00Z", distance_meters: null, duration_seconds: 1500, kilocalories: null, average_bpm: 118 },
    ];
    const distance = swimmingChartOption(points, "distance_meters");
    const duration = swimmingChartOption(points, "duration_seconds");
    expect(distance.series).toEqual([expect.objectContaining({ data: [1000, null] })]);
    expect(duration.series).toEqual([expect.objectContaining({ data: [30, 25] })]);
    expect(points[0].duration_seconds).toBe(1800);
    for (const theme of ["light", "dark", "ocean", "sunset"] as ThemeName[]) {
      expect(chartOptionForTheme(distance, theme).series).toEqual([expect.objectContaining({ data: [1000, null] })]);
    }
  });

  it("plots calories and average heart rate with missing values preserved", () => {
    const option = swimmingIntensityChartOption([
      { start_time: "2026-09-01T09:00:00Z", distance_meters: 1000, duration_seconds: 1800, kilocalories: 240, average_bpm: 126 },
      { start_time: "2026-09-02T09:00:00Z", distance_meters: null, duration_seconds: 1500, kilocalories: null, average_bpm: null },
    ]) as any;
    expect(option.yAxis.map((axis: any) => axis.name)).toEqual(["ккал", "уд/мин"]);
    expect(option.series.map((series: any) => series.name)).toEqual(["Активные калории", "Средний пульс"]);
    expect(option.series[0].data).toEqual([240, null]);
    expect(option.series[1].data).toEqual([126, null]);
  });
});
