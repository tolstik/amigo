import { useCallback } from "react";
import { Navigate, Route, Routes } from "react-router-dom";
import { api } from "./api/client";
import type { Overview } from "./api/types";
import { AppLayout } from "./components/AppLayout";
import { useApi, type ApiState } from "./hooks/useApi";
import { CompositionPage } from "./pages/CompositionPage";
import { ActivityPage } from "./pages/ActivityPage";
import { HistoryPage } from "./pages/HistoryPage";
import { OverviewPage } from "./pages/OverviewPage";
import { PressurePage } from "./pages/PressurePage";
import { ProgressPage } from "./pages/ProgressPage";
import { RecoveryPage } from "./pages/RecoveryPage";

export type OverviewContext = ApiState<Overview>;

export function App() {
  const loadOverview = useCallback((signal: AbortSignal) => api.overview(signal), []);
  const overview = useApi(loadOverview);

  return (
    <Routes>
      <Route element={<AppLayout overview={overview} />}>
        <Route index element={<OverviewPage />} />
        <Route path="progress" element={<ProgressPage />} />
        <Route path="history" element={<HistoryPage />} />
        <Route path="pressure" element={<PressurePage />} />
        <Route path="composition" element={<CompositionPage />} />
        <Route path="activity" element={<ActivityPage />} />
        <Route path="recovery" element={<RecoveryPage />} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Route>
    </Routes>
  );
}
