import { useCallback, useEffect, useState } from "react";
import { Navigate, Route, Routes } from "react-router-dom";
import { api } from "./api/client";
import type { AuthSession, Overview } from "./api/types";
import { AppLayout } from "./components/AppLayout";
import { useApi, type ApiState } from "./hooks/useApi";
import { CompositionPage } from "./pages/CompositionPage";
import { ActivityPage } from "./pages/ActivityPage";
import { HistoryPage } from "./pages/HistoryPage";
import { OverviewPage } from "./pages/OverviewPage";
import { PressurePage } from "./pages/PressurePage";
import { ProgressPage } from "./pages/ProgressPage";
import { RecoveryPage } from "./pages/RecoveryPage";
import { AssistantPage } from "./pages/AssistantPage";
import { LabAnalytePage } from "./pages/LabAnalytePage";
import { LabDocumentPage } from "./pages/LabDocumentPage";
import { LabsPage } from "./pages/LabsPage";
import { LabsUploadPage } from "./pages/LabsUploadPage";
import { LoginPage } from "./pages/LoginPage";
import { ProfilePage } from "./pages/ProfilePage";

export type OverviewContext = ApiState<Overview>;

function PrivateApp({ session, onLogout }: { session: AuthSession; onLogout: () => void }) {
  const loadOverview = useCallback((signal: AbortSignal) => api.overview(signal), []);
  const overview = useApi(loadOverview);

  return (
    <Routes>
      <Route element={<AppLayout overview={overview} session={session} onLogout={onLogout} />}>
        <Route index element={<OverviewPage />} />
        <Route path="progress" element={<ProgressPage />} />
        <Route path="history" element={<HistoryPage />} />
        <Route path="pressure" element={<PressurePage />} />
        <Route path="composition" element={<CompositionPage />} />
        <Route path="activity" element={<ActivityPage />} />
        <Route path="recovery" element={<RecoveryPage />} />
        <Route path="labs" element={<LabsPage />} />
        <Route path="labs/upload" element={<LabsUploadPage />} />
        <Route path="labs/documents/:id" element={<LabDocumentPage />} />
        <Route path="labs/analytes/:id" element={<LabAnalytePage />} />
        <Route path="assistant" element={<AssistantPage />} />
        <Route path="profile" element={<ProfilePage onLogout={onLogout} />} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Route>
    </Routes>
  );
}

export function App() {
  const [session, setSession] = useState<AuthSession | null | undefined>(undefined);
  useEffect(() => {
    const controller = new AbortController();
    api.session(controller.signal).then(setSession).catch(() => setSession(null));
    const unauthorized = () => setSession(null);
    window.addEventListener("amigo:unauthorized", unauthorized);
    return () => { controller.abort(); window.removeEventListener("amigo:unauthorized", unauthorized); };
  }, []);
  if (session === undefined) return <div className="login-shell"><span className="spinner" /><span>Проверяем доступ…</span></div>;
  if (session === null) return <LoginPage onLogin={setSession} />;
  return <PrivateApp session={session} onLogout={() => setSession(null)} />;
}
