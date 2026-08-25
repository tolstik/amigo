import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { PressureCategoryGuide } from "./PressureCategoryGuide";

describe("PressureCategoryGuide", () => {
  it("shows exact boundaries, a cautious disclaimer and an accessible daily table", async () => {
    const user = userEvent.setup();
    render(
      <PressureCategoryGuide days={[{
        date: "2026-08-25",
        category: "critical_high",
        sessions: 2,
        minSystolic: 128,
        maxSystolic: 181,
        minDiastolic: 78,
        maxDiastolic: 86,
      }]} />,
    );

    const legend = screen.getByRole("list", { name: "Границы категорий давления" });
    expect(legend).toHaveTextContent("Ниже ориентира");
    expect(legend).toHaveTextContent("сист. 90–134 и диаст. 60–84");
    expect(legend).toHaveTextContent("сист. 135–179 или диаст. 85–119");
    expect(legend).toHaveTextContent("сист. ≥ 180 или диаст. ≥ 120");
    expect(screen.getByText(/Это визуальный ориентир, а не диагноз/)).toBeVisible();
    expect(screen.getByText(/обращайтесь за экстренной медицинской помощью/)).toBeVisible();

    await user.click(screen.getByText("Показать дневные категории (1)"));
    const table = screen.getByRole("table", { name: "Дневные категории давления и диапазоны сессий" });
    expect(table).toHaveTextContent("Критически высокое");
    expect(table).toHaveTextContent("128–181 / 78–86");
  });

  it("does not show an emergency prompt when the history has no red day", () => {
    render(<PressureCategoryGuide days={[{
      date: "2026-08-24",
      category: "home_guide",
      sessions: 1,
      minSystolic: 122,
      maxSystolic: 122,
      minDiastolic: 78,
      maxDiastolic: 78,
    }]} />);

    expect(screen.getByText(/Это визуальный ориентир, а не диагноз/)).toBeVisible();
    expect(screen.queryByText(/В истории есть значение в красной категории/)).not.toBeInTheDocument();
    expect(screen.queryByText(/экстренной медицинской помощью/)).not.toBeInTheDocument();
  });
});
