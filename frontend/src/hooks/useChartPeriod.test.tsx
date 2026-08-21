import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { CHART_PERIOD_STORAGE_KEY, useChartPeriod } from "./useChartPeriod";

function Harness({ fallback = "90d" }: { fallback?: "30d" | "90d" | "1y" | "all" }) {
  const [period, setPeriod] = useChartPeriod(fallback);
  return <button onClick={() => setPeriod("1y")}>{period}</button>;
}

describe("useChartPeriod", () => {
  beforeEach(() => window.localStorage.clear());
  afterEach(cleanup);

  it("persists an explicit selection and restores it for another chart", async () => {
    const user = userEvent.setup();
    const first = render(<Harness />);

    await user.click(screen.getByRole("button", { name: "90d" }));
    expect(window.localStorage.getItem(CHART_PERIOD_STORAGE_KEY)).toBe("1y");
    first.unmount();
    render(<Harness fallback="all" />);

    expect(screen.getByRole("button", { name: "1y" })).toBeInTheDocument();
  });

  it("ignores an unsupported stored value", () => {
    window.localStorage.setItem(CHART_PERIOD_STORAGE_KEY, "program");
    render(<Harness fallback="30d" />);

    expect(screen.getByRole("button", { name: "30d" })).toBeInTheDocument();
  });
});
