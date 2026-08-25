import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { EChartsOption } from "echarts";
import type { ReactNode } from "react";
import type { HeartRateHourlyPoint } from "../api/types";
import { WatchHeartRateChart } from "./WatchHeartRateChart";

vi.mock("./ChartCard", () => ({
  ChartCard: ({ subtitle, option, aside, footer }: {
    subtitle?: string;
    option: EChartsOption;
    aside?: ReactNode;
    footer?: ReactNode;
  }) => {
    const average = (option.series as Array<{ name?: string; data?: Array<[string, number | null]> }>)
      .find((series) => series.name === "Средний");
    const pointCount = average?.data?.filter((item) => item[1] !== null).length ?? 0;
    const zoomTypes = (option.dataZoom as Array<{ type?: string }>).map((item) => item.type).join(",");
    return (
      <section>
        <p>{subtitle}</p>
        {aside}
        <output data-testid="heart-rate-point-count">{pointCount}</output>
        <output data-testid="heart-rate-zoom-types">{zoomTypes}</output>
        {footer}
      </section>
    );
  },
}));

function point(measuredAt: string, averageBpm: number): HeartRateHourlyPoint {
  return { measuredAt, averageBpm, minimumBpm: averageBpm - 10, maximumBpm: averageBpm + 10, sampleCount: 2 };
}

describe("WatchHeartRateChart", () => {
  it("adapts the automatic resolution to new data until the user chooses one", async () => {
    const user = userEvent.setup();
    const { rerender } = render(<WatchHeartRateChart points={[
      point("2026-07-20T09:00:00+03:00", 70),
      point("2026-07-20T10:00:00+03:00", 80),
      point("2026-08-20T09:00:00+03:00", 75),
    ]} />);

    expect(screen.getByRole("button", { name: "6 ч" })).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByTestId("heart-rate-point-count")).toHaveTextContent("2");
    expect(screen.getByTestId("heart-rate-zoom-types")).toHaveTextContent("inside,slider");
    expect(screen.getByText(/Пропуски данных показаны разрывами/)).toBeInTheDocument();

    rerender(<WatchHeartRateChart points={[
      point("2026-08-19T09:00:00+03:00", 70),
      point("2026-08-20T09:00:00+03:00", 75),
    ]} />);

    expect(screen.getByRole("button", { name: "1 ч" })).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByText(/Интервал: 1 час/)).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "3 ч" }));
    rerender(<WatchHeartRateChart points={[
      point("2026-06-20T09:00:00+03:00", 70),
      point("2026-08-20T09:00:00+03:00", 75),
    ]} />);

    expect(screen.getByRole("button", { name: "3 ч" })).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByText(/Интервал: 3 часа/)).toBeInTheDocument();
  });
});
