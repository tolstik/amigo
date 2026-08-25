import type { PressurePoint } from "../api/types";
import { aggregateDailyPressureCategories, classifyPressureCategory } from "./pressureCategories";

function point(measuredAt: string, systolic: number, diastolic: number): PressurePoint {
  return {
    measuredAt,
    systolic,
    diastolic,
    pulse: null,
    pulsePressure: systolic - diastolic,
    sessionSize: 1,
    periodOfDay: "other",
  };
}

describe("pressure category classification", () => {
  it("keeps all boundaries explicit and gives a higher category priority over a low component", () => {
    expect(classifyPressureCategory(90, 60)).toBe("home_guide");
    expect(classifyPressureCategory(134, 84)).toBe("home_guide");
    expect(classifyPressureCategory(89, 84)).toBe("below_guide");
    expect(classifyPressureCategory(120, 59)).toBe("below_guide");
    expect(classifyPressureCategory(135, 70)).toBe("elevated");
    expect(classifyPressureCategory(120, 85)).toBe("elevated");
    expect(classifyPressureCategory(89, 85)).toBe("elevated");
    expect(classifyPressureCategory(180, 70)).toBe("critical_high");
    expect(classifyPressureCategory(120, 120)).toBe("critical_high");
  });

  it("selects the category needing the most attention per Moscow day and preserves daily ranges", () => {
    const days = aggregateDailyPressureCategories([
      point("2026-08-24T20:30:00Z", 122, 78),
      point("2026-08-24T21:30:00Z", 88, 58),
      point("2026-08-25T06:00:00Z", 142, 80),
      point("2026-08-25T18:00:00Z", 181, 82),
    ]);

    expect(days).toEqual([
      {
        date: "2026-08-24",
        category: "home_guide",
        sessions: 1,
        minSystolic: 122,
        maxSystolic: 122,
        minDiastolic: 78,
        maxDiastolic: 78,
      },
      {
        date: "2026-08-25",
        category: "critical_high",
        sessions: 3,
        minSystolic: 88,
        maxSystolic: 181,
        minDiastolic: 58,
        maxDiastolic: 82,
      },
    ]);

    expect(aggregateDailyPressureCategories([
      point("2026-08-26T06:00:00Z", 122, 78),
      point("2026-08-26T18:00:00Z", 88, 58),
    ])[0].category).toBe("below_guide");
  });
});
