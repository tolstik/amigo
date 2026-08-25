import { render, screen } from "@testing-library/react";
import type { HealthCorrelation } from "../api/types";
import { CorrelationPanel, describeCorrelation } from "./CorrelationPanel";

const correlations: HealthCorrelation[] = [
  {
    metric: "sleep_minutes",
    target: "systolic_mm_hg",
    coefficient: -0.42,
    fullOverlappingWeeks: 16,
    disclaimer: "Корреляция не доказывает причинность.",
  },
  {
    metric: "resting_heart_rate_bpm",
    target: "weight_kg",
    coefficient: 0.12,
    fullOverlappingWeeks: 8,
    disclaimer: "Корреляция не доказывает причинность.",
  },
];

describe("CorrelationPanel", () => {
  it("explains r and renders readable interpretations without claiming causality", () => {
    render(<CorrelationPanel
      id="correlations"
      correlations={correlations}
      metricLabels={{
        sleep_minutes: "Продолжительность сна",
        systolic_mm_hg: "Систолическое давление",
        resting_heart_rate_bpm: "Пульс покоя",
        weight_kg: "Вес",
      }}
    />);

    expect(screen.getByRole("heading", { name: "Совместная динамика" })).toBeVisible();
    expect(screen.getByText(/r — коэффициент линейной корреляции Пирсона от −1 до 1/)).toBeVisible();
    expect(screen.getByText("Умеренная обратная линейная связь.")).toBeVisible();
    expect(screen.getByText("Линейная связь почти не выражена.")).toBeVisible();
    expect(screen.getByText(/Корреляция не доказывает причинность/)).toBeVisible();
    expect(screen.getByText("r = -0,42")).toBeVisible();
  });

  it("uses bounded, symmetric verbal ranges", () => {
    expect(describeCorrelation(-0.19)).toBe("Линейная связь почти не выражена.");
    expect(describeCorrelation(-0.2)).toBe("Слабая обратная линейная связь.");
    expect(describeCorrelation(0.4)).toBe("Умеренная прямая линейная связь.");
    expect(describeCorrelation(0.7)).toBe("Сильная прямая линейная связь.");
  });
});
