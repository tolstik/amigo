import { useCallback, useState } from "react";
import type { Period } from "../api/types";

export const CHART_PERIOD_STORAGE_KEY = "amigo.chart.period.v1";

const selectablePeriods = new Set<Period>(["30d", "90d", "1y", "all"]);

function storedPeriod(fallback: Period): Period {
  try {
    const value = window.localStorage.getItem(CHART_PERIOD_STORAGE_KEY) as Period | null;
    return value && selectablePeriods.has(value) ? value : fallback;
  } catch {
    return fallback;
  }
}

export function useChartPeriod(fallback: Period): [Period, (period: Period) => void] {
  const [period, setPeriodState] = useState<Period>(() => storedPeriod(fallback));
  const setPeriod = useCallback((value: Period) => {
    const next = selectablePeriods.has(value) ? value : fallback;
    setPeriodState(next);
    try {
      window.localStorage.setItem(CHART_PERIOD_STORAGE_KEY, next);
    } catch {
      // Storage can be unavailable in a restricted WebView; the current page still updates.
    }
  }, [fallback]);
  return [period, setPeriod];
}
