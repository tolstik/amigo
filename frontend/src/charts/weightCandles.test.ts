import { dailyWeightChartOption } from "./options";
import { chartOptionForTheme, chartPalettes } from "./theme";
import { dailyWeightCandles, weightCandleChange, weightCandleDates } from "./weightCandles";

const asOf = "2026-09-07T12:00:00Z";

describe("daily weight candles", () => {
  it("excludes July 31 Moscow measurements from candles and subsequent comparisons without changing source data", () => {
    const raw = [
      { measuredAt: "2026-07-30T20:59:59Z", valueKg: 126 },
      { measuredAt: "2026-07-30T21:00:00Z", valueKg: 124 },
      { measuredAt: "2026-07-31T20:59:59Z", valueKg: 123 },
      { measuredAt: "2026-07-31T21:00:00Z", valueKg: 125.5 },
    ];
    const original = structuredClone(raw);
    const candles = dailyWeightCandles(raw, asOf);
    expect(candles.map(point => point.date)).toEqual(["2026-07-30", "2026-08-01"]);
    expect(candles[1]).toMatchObject({ previousDate: "2026-07-30", previousKg: 126, lastKg: 125.5 });
    expect(weightCandleChange(candles[1])).toBe(-0.5);
    const option = dailyWeightChartOption(candles, asOf) as any;
    expect(option.series[0].data[option.xAxis.data.indexOf("2026-07-31")]).toEqual(["-", "-", "-", "-"]);
    expect(option.series[0].data[option.xAxis.data.indexOf("2026-08-01")]).toEqual([126, 125.5, 125.5, 126]);
    expect(dailyWeightCandles(raw.slice(1), asOf)[0].previousKg).toBeNull();
    expect(raw).toEqual(original);
  });

  it("spans the full change between single daily weighings, including the anchor outside the period", () => {
    const candles = dailyWeightCandles([
      { measuredAt: "2026-06-09T06:00:00Z", valueKg: 125.9 },
      { measuredAt: "2026-06-10T06:00:00Z", valueKg: 125.4 },
      { measuredAt: "2026-06-11T06:00:00Z", valueKg: 125.6 },
    ], asOf);
    expect(candles.map(point => point.date)).toEqual(["2026-06-10", "2026-06-11"]);
    expect(candles[0]).toMatchObject({ previousKg: 125.9, previousDate: "2026-06-09", lastKg: 125.4, sampleCount: 1 });
    expect(weightCandleChange(candles[0])).toBe(-0.5);
    expect(weightCandleChange(candles[1])).toBe(0.2);
    const option = dailyWeightChartOption(candles, asOf) as any;
    expect(option.series[0].data.slice(0, 2)).toEqual([[125.9, 125.4, 125.4, 125.9], [125.4, 125.6, 125.4, 125.6]]);
    expect(option.tooltip.formatter([{ axisValue: "2026-06-10" }])).toContain("−0,50 кг");
  });

  it("orders mixed timezone timestamps and keeps daily extremes across Moscow midnight", () => {
    const raw = [
      { measuredAt: "2026-09-01T20:30:00Z", valueKg: 125.4 },
      { measuredAt: "2026-09-01T10:00:00Z", valueKg: 125.2 },
      { measuredAt: "2026-09-01T21:00:00Z", valueKg: 125.3 },
      { measuredAt: "2026-09-01T12:00:00Z", valueKg: 126.1 },
      { measuredAt: "2026-08-31T22:00:00Z", valueKg: 125.8 },
      { measuredAt: "2026-09-01T00:00:00+03:00", valueKg: 125.9 },
      { measuredAt: "2026-08-31T06:00:00Z", valueKg: 126 },
    ];
    const original = [...raw];
    const candles = dailyWeightCandles(raw, asOf);
    expect(candles).toEqual([
      { date: "2026-08-31", previousKg: null, previousDate: null, lastKg: 126, minimumKg: 126, maximumKg: 126, sampleCount: 1 },
      { date: "2026-09-01", previousKg: 126, previousDate: "2026-08-31", lastKg: 125.4, minimumKg: 125.2, maximumKg: 126.1, sampleCount: 5 },
      { date: "2026-09-02", previousKg: 125.4, previousDate: "2026-09-01", lastKg: 125.3, minimumKg: 125.3, maximumKg: 125.3, sampleCount: 1 },
    ]);
    expect(raw).toEqual(original);
    const option = dailyWeightChartOption(candles, asOf) as any;
    expect(option.series[0].data.at(-7)).toEqual([126, 125.4, 125.2, 126.1]);
    const tooltip = option.tooltip.formatter([{ axisValue: "2026-09-01" }]);
    for (const text of ["Вес 31 авг.", "Последний замер", "Минимум за день", "Максимум за день", "−0,60 кг", "МСК"]) {
      expect(tooltip).toContain(text);
    }
  });

  it("leaves missing days empty and labels comparison to the actual previous measured day", () => {
    const candles = dailyWeightCandles([
      { measuredAt: "2026-08-31T06:00:00Z", valueKg: 126 },
      { measuredAt: "2026-09-02T06:00:00Z", valueKg: 125.5 },
      { measuredAt: "2026-09-03T06:00:00Z", valueKg: 125.5 },
    ], asOf);
    const option = dailyWeightChartOption(candles, asOf) as any;
    expect(option.series[0].data.slice(-7, -4)).toEqual([["-", "-", "-", "-"], [126, 125.5, 125.5, 126], [125.5, 125.5, 125.5, 125.5]]);
    const tooltip = (date: string) => option.tooltip.formatter([{ axisValue: date }]);
    expect(tooltip("2026-09-01")).toContain("Нет замеров");
    expect(tooltip("2026-09-02")).toContain("Вес 31 авг.");
    expect(tooltip("2026-09-02")).toContain("−0,50 кг");
    expect(tooltip("2026-09-03")).toContain("0,00 кг");
  });

  it("shows exactly 90 Moscow calendar days ending today, regardless of stale or future measurements", () => {
    const midnight = "2026-09-06T21:00:00Z";
    const candles = dailyWeightCandles([
      { measuredAt: "2026-06-09T06:00:00Z", valueKg: 126 },
      { measuredAt: "2026-09-08T06:00:00Z", valueKg: 125 },
    ], midnight);
    const option = dailyWeightChartOption(candles, midnight) as any;
    expect(option.xAxis.data).toHaveLength(90);
    expect(option.xAxis.data[0]).toBe("2026-06-10");
    expect(option.xAxis.data.at(-1)).toBe("2026-09-07");
    expect(weightCandleDates("2026-09-06T20:59:59Z").at(-1)).toBe("2026-09-06");
    expect(candles).toEqual([]);
    expect(option.dataZoom).toBeUndefined();
    expect(option.series[0].data).toHaveLength(90);
    expect(option.series[0].data.every((values: string[]) => values.every(value => value === "-"))).toBe(true);
  });

  it("does not invent a change when there is no previous weighing", () => {
    const candles = dailyWeightCandles([
      { measuredAt: "invalid", valueKg: 125 },
      { measuredAt: "2026-09-01T05:00:00Z", valueKg: NaN },
      { measuredAt: "2026-09-01T06:00:00Z", valueKg: 125.5 },
    ], asOf);
    expect(candles).toHaveLength(1);
    expect(weightCandleChange(candles[0])).toBeNull();
    const option = dailyWeightChartOption(candles, asOf) as any;
    expect(option.series[0].data.at(-7)).toEqual([125.5, 125.5, 125.5, 125.5]);
    expect(option.tooltip.formatter([{ axisValue: "2026-09-01" }])).toContain("Нет предыдущего замера для сравнения");
    expect(dailyWeightCandles([], asOf)).toEqual([]);
  });

  it("keeps rising, falling and unchanged candle colors consistent in every theme", () => {
    const option = dailyWeightChartOption([], asOf);
    for (const theme of ["light", "dark", "ocean", "sunset"] as const) {
      const themed = chartOptionForTheme(option, theme) as any;
      expect(themed.series[0].type).toBe("candlestick");
      expect(themed.series[0].itemStyle).toMatchObject({
        color: chartPalettes[theme].coral, borderColor: chartPalettes[theme].coral,
        color0: chartPalettes[theme].green, borderColor0: chartPalettes[theme].green,
        borderColorDoji: chartPalettes[theme].muted,
      });
    }
  });
});
