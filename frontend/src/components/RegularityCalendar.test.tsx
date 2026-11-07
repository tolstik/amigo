import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import type { WeightPoint } from "../api/types";
import { buildCalendar, RegularityCalendar } from "./RegularityCalendar";

function point(day: string, weightKg: number, plannedKg: number | null, isOutlier = false): WeightPoint {
  return { measuredAt: day, weightKg, smoothed7dKg: null, plannedKg, forecastKg: null, forecastLowKg: null, forecastHighKg: null, isOutlier };
}

describe("RegularityCalendar", () => {
  it("aligns Monday-first weeks and classifies days against the same-day plan", () => {
    // 2026-08-15 is a Saturday.
    const weeks = buildCalendar([
      point("2026-08-15", 127.0, 127.03),
      point("2026-08-16", 127.5, 126.9),
      point("2026-08-17", 126.0, 126.8),
      point("2026-08-18", 120.0, 126.7, true),
    ], "2026-08-15", "2026-08-19");
    expect(weeks).toHaveLength(2);
    expect(weeks[0][0].day).toBe("2026-08-10");
    expect(weeks[0].slice(0, 5).every((cell) => cell.state === "future")).toBe(true);
    expect(weeks[0][5].state).toBe("near");
    expect(weeks[0][6].state).toBe("behind");
    expect(weeks[1][0].state).toBe("ahead");
    expect(weeks[1][1].state).toBe("outlier");
    expect(weeks[1][2].state).toBe("empty");
    expect(weeks[1][3].state).toBe("future");
  });

  it("labels each elapsed day for assistive technology", () => {
    render(<RegularityCalendar points={[point("2026-08-15", 127.0, 127.03)]} startDate="2026-08-15" today="2026-08-16" />);
    expect(screen.getByText("из 2 дней", { exact: false })).toBeInTheDocument();
    expect(screen.getByLabelText(/15 авг.*рядом с планом/)).toBeInTheDocument();
    expect(screen.getByLabelText(/16 авг.*нет замера/)).toBeInTheDocument();
  });
});
