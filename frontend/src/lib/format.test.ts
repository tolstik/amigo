import { clampProgress, formatDelta, formatKg, periodLabels } from "./format";

describe("format helpers", () => {
  it("formats weight and signed change for Russian UI", () => {
    expect(formatKg(126.42)).toBe("126,4 кг");
    expect(formatDelta(-3.25)).toBe("−3,3 кг");
    expect(formatDelta(1.04)).toBe("+1,0 кг");
  });

  it("keeps absent values explicit", () => {
    expect(formatKg(null)).toBe("—");
    expect(formatDelta(undefined)).toBe("—");
  });

  it("clamps progress only for visual display", () => {
    expect(clampProgress(-20)).toBe(0);
    expect(clampProgress(37.4)).toBe(37.4);
    expect(clampProgress(120)).toBe(100);
    expect(periodLabels.program).toBe("С начала плана");
  });
});
