import { useEffect, useState } from "react";

export const LOADING_START_EVENT = "amigo:loading:start";
export const LOADING_END_EVENT = "amigo:loading:end";

export function GlobalLoadingPopup() {
  const [pending, setPending] = useState(0);
  useEffect(() => {
    const start = () => setPending((value) => value + 1);
    const end = () => setPending((value) => Math.max(0, value - 1));
    window.addEventListener(LOADING_START_EVENT, start);
    window.addEventListener(LOADING_END_EVENT, end);
    return () => {
      window.removeEventListener(LOADING_START_EVENT, start);
      window.removeEventListener(LOADING_END_EVENT, end);
    };
  }, []);
  if (!pending) return null;
  return <div className="loading-popup" role="status" aria-live="polite" aria-label="Идёт загрузка">
    <div className="loading-popup__card"><span className="spinner" /><strong>Пожалуйста, подождите</strong><span>Запрос выполняется…</span></div>
  </div>;
}
