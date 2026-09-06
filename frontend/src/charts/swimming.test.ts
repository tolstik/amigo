import { describe, expect, it } from "vitest";
import { swimmingChartOption } from "./options";
import { chartOptionForTheme } from "./theme";
import type { ThemeName } from "../theme/ThemeProvider";

describe("pool charts", () => {
  it("keeps missing distances empty and converts only chart duration to minutes", () => {
    const points = [
      { start_time: "2026-09-01T09:00:00Z", distance_meters: 1000, duration_seconds: 1800 },
      { start_time: "2026-09-02T09:00:00Z", distance_meters: null, duration_seconds: 1500 },
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
});
