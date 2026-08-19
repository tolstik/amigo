import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { PeriodSwitcher } from "./PeriodSwitcher";

describe("PeriodSwitcher", () => {
  it("announces the selected interval and changes it", async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    render(<PeriodSwitcher value="90d" onChange={onChange} options={["30d", "90d", "all"]} />);

    expect(screen.getByRole("button", { name: "90 дней" })).toHaveAttribute("aria-pressed", "true");
    await user.click(screen.getByRole("button", { name: "Всё время" }));
    expect(onChange).toHaveBeenCalledWith("all");
  });
});
