import { dailyWeightChartOption } from "./options";
import { chartOptionForTheme, chartPalettes } from "./theme";
import { dailyWeightCandles, weightCandleChange } from "./weightCandles";

describe("daily weight candles", () => {
  it("uses chronological measurements and Moscow midnight for real OHLC values", () => {
    const raw = [
      { measuredAt: "2026-09-01T20:30:00Z", valueKg: 125.4 },
      { measuredAt: "2026-09-01T10:00:00Z", valueKg: 125.2 },
      { measuredAt: "2026-09-01T21:00:00Z", valueKg: 125.3 },
      { measuredAt: "2026-09-01T12:00:00Z", valueKg: 126.1 },
      { measuredAt: "2026-08-31T22:00:00Z", valueKg: 125.8 },
      { measuredAt: "2026-09-01T00:00:00+03:00", valueKg: 125.9 },
    ];
    const original = [...raw];
    const candles = dailyWeightCandles(raw);
    expect(candles).toEqual([
      { date: "2026-09-01", firstKg: 125.9, lastKg: 125.4, minimumKg: 125.2, maximumKg: 126.1, sampleCount: 5 },
      { date: "2026-09-02", firstKg: 125.3, lastKg: 125.3, minimumKg: 125.3, maximumKg: 125.3, sampleCount: 1 },
    ]);
    expect(raw).toEqual(original);
    expect(weightCandleChange(candles[0])).toBe(-0.5);
    expect(weightCandleChange(candles[1])).toBeNull();
  });

  it("keeps missing calendar days empty and distinguishes a single sample from no change", () => {
    const candles = dailyWeightCandles([
      { measuredAt: "2026-08-31T06:00:00Z", valueKg: 126 },
      { measuredAt: "2026-09-02T06:00:00Z", valueKg: 125.5 },
      { measuredAt: "2026-09-02T18:00:00Z", valueKg: 125.5 },
    ]);
    const option = dailyWeightChartOption(candles) as any;
    expect(option.xAxis.data).toEqual(["2026-08-31", "2026-09-01", "2026-09-02"]);
    expect(option.series[0].data).toEqual([[126, 126, 126, 126], ["-", "-", "-", "-"], [125.5, 125.5, 125.5, 125.5]]);
    const tooltip = (date: string) => option.tooltip.formatter([{ axisValue: date }]);
    expect(tooltip("2026-08-31")).toContain("Один замер — изменение неизвестно");
    expect(tooltip("2026-09-01")).toContain("Нет замеров");
    expect(tooltip("2026-09-02")).toContain("0,00 кг");
    expect(tooltip("2026-09-02")).not.toContain("изменение неизвестно");
  });

  it("renders open, close, low, high in ECharts order and reports the signed daily change", () => {
    const candles = dailyWeightCandles([
      { measuredAt: "2026-09-01T06:00:00Z", valueKg: 125.9 },
      { measuredAt: "2026-09-01T12:00:00Z", valueKg: 126.1 },
      { measuredAt: "2026-09-01T14:00:00Z", valueKg: 125.2 },
      { measuredAt: "2026-09-01T18:00:00Z", valueKg: 125.4 },
    ]);
    const option = dailyWeightChartOption(candles) as any;
    expect(option.series[0].type).toBe("candlestick");
    expect(option.series[0].data).toEqual([[125.9, 125.4, 125.2, 126.1]]);
    const tooltip = option.tooltip.formatter([{ axisValue: "2026-09-01" }]);
    for (const text of ["Первый замер", "Последний замер", "Минимум", "Максимум", "−0,50 кг", "МСК", "Всего замеров"]) {
      expect(tooltip).toContain(text);
    }
    for (const theme of ["light", "dark", "ocean", "sunset"] as const) {
      const themed = chartOptionForTheme(option, theme) as any;
      expect(themed.series[0].itemStyle).toMatchObject({
        color: chartPalettes[theme].coral, borderColor: chartPalettes[theme].coral,
        color0: chartPalettes[theme].green, borderColor0: chartPalettes[theme].green,
        borderColorDoji: chartPalettes[theme].muted,
      });
    }
  });

  it("offers visible zoom over the full history starting on the latest 30 calendar days", () => {
    const option = dailyWeightChartOption(dailyWeightCandles([
      { measuredAt: "2026-08-01T06:00:00Z", valueKg: 126 },
      { measuredAt: "2026-09-01T06:00:00Z", valueKg: 125 },
    ])) as any;
    expect(option.dataZoom.map((zoom: any) => zoom.type)).toEqual(["inside", "slider"]);
    expect(option.dataZoom[1]).toMatchObject({ startValue: 2, endValue: 31 });
  });

  it("does not invent candles for empty or invalid measurements", () => {
    const candles = dailyWeightCandles([
      { measuredAt: "invalid", valueKg: 125 },
      { measuredAt: "2026-09-01T06:00:00Z", valueKg: NaN },
    ]);
    expect(candles).toEqual([]);
    const option = dailyWeightChartOption(candles) as any;
    expect(option.series[0].data).toEqual([]);
    expect(option.xAxis.data).toEqual([]);
  });
});
