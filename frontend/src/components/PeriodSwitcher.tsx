import type { Period } from "../api/types";
import { periodLabels } from "../lib/format";

interface PeriodSwitcherProps {
  value: Period;
  onChange: (period: Period) => void;
  options?: Period[];
  label?: string;
}

export function PeriodSwitcher({
  value,
  onChange,
  options = ["30d", "90d", "1y", "all"],
  label = "Период графика",
}: PeriodSwitcherProps) {
  return (
    <div className="period-switcher" role="group" aria-label={label}>
      {options.map((period) => (
        <button
          key={period}
          type="button"
          className={period === value ? "is-active" : ""}
          aria-pressed={period === value}
          onClick={() => onChange(period)}
        >
          {periodLabels[period]}
        </button>
      ))}
    </div>
  );
}
