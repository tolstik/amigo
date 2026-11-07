import { describe, expect, it } from "vitest";
import { greeting, moscowToday, shiftIsoDate } from "./format";
import { dailyTrend, hasTrend } from "./trend";
import { sparklinePath } from "../components/Sparkline";

describe("daily trend alignment", () => {
  it("keeps missing Moscow days empty", () => {
    const points = [
      { day: "2026-09-01", value: 120 },
      { day: "2026-09-03", value: 119.5 },
    ];
    expect(dailyTrend(points, (point) => point.day, (point) => point.value, 4, "2026-09-03")).toEqual([null, 120, null, 119.5]);
    expect(hasTrend([null, 1, null])).toBe(false);
  });

  it("shifts dates without timezone drift and greets by Moscow time", () => {
    expect(shiftIsoDate("2026-03-01", -1)).toBe("2026-02-28");
    expect(moscowToday(new Date("2026-09-01T21:30:00Z"))).toBe("2026-09-02");
    expect(greeting(new Date("2026-09-01T04:00:00Z"))).toBe("Доброе утро");
    expect(greeting(new Date("2026-09-01T10:00:00Z"))).toBe("Добрый день");
    expect(greeting(new Date("2026-09-01T16:00:00Z"))).toBe("Добрый вечер");
    expect(greeting(new Date("2026-09-01T22:00:00Z"))).toBe("Доброй ночи");
  });

  it("breaks sparkline segments at gaps", () => {
    const path = sparklinePath([1, 2, null, 3, 4]);
    expect(path.match(/M/g)).toHaveLength(2);
    expect(sparklinePath([1, null])).toBe("");
  });
});
