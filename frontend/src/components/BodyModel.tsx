import { useEffect, useRef, useState } from "react";
import { createBodyScene, type BodyModelAssets, type BodySceneHandle } from "./bodyScene";
import { formatDateTime, formatNumber } from "../lib/format";
import { useTheme } from "../theme/ThemeProvider";

type View = "front" | "side" | "back";

interface BodyModelProps {
  latestKg: number | null;
  latestAt: string | null;
  heightCm: number;
  startKg: number;
  targetKg: number;
}

const weightFormatter = new Intl.NumberFormat("ru-RU", {
  minimumFractionDigits: 1,
  maximumFractionDigits: 3,
});

function weightLabel(weight: number): string {
  return weightFormatter.format(weight) + " кг";
}

export default function BodyModel({ latestKg, latestAt, heightCm, startKg, targetKg }: BodyModelProps) {
  const { theme } = useTheme();
  const host = useRef<HTMLDivElement>(null);
  const scene = useRef<BodySceneHandle | null>(null);
  const [assets, setAssets] = useState<BodyModelAssets | null>(null);
  const [nearViewport, setNearViewport] = useState(false);
  const [unavailable, setUnavailable] = useState(false);
  const [rotating, setRotating] = useState(() => !window.matchMedia("(prefers-reduced-motion: reduce)").matches);
  const [selectedView, setSelectedView] = useState<View | null>(null);
  const [closeUp, setCloseUp] = useState(false);
  const [hoodie, setHoodie] = useState(true);
  const [glasses, setGlasses] = useState(false);

  useEffect(() => {
    const element = host.current;
    if (!element) return;
    if (typeof IntersectionObserver === "undefined") { setNearViewport(true); return; }
    const observer = new IntersectionObserver(([entry]) => {
      if (entry.isIntersecting) { setNearViewport(true); observer.disconnect(); }
    }, { rootMargin: "400px 0px" });
    observer.observe(element);
    return () => observer.disconnect();
  }, []);

  useEffect(() => {
    if (!nearViewport) return;
    const controller = new AbortController();
    const basePath = import.meta.env.BASE_URL.replace(/\/$/, "");
    fetch(basePath + "/api/v1/profile/body-model-assets", {
      credentials: "same-origin", cache: "no-store", signal: controller.signal,
    }).then(async (response) => {
      if (!response.ok || !response.headers.get("Content-Type")?.startsWith("application/json")) return;
      const value: unknown = await response.json();
      if (controller.signal.aborted || !value || typeof value !== "object") return;
      const candidate = value as Partial<BodyModelAssets>;
      if (candidate.version === 1 && candidate.head && candidate.ear) setAssets(candidate as BodyModelAssets);
    }).catch(() => { /* The neutral head remains available without private assets. */ });
    return () => controller.abort();
  }, [nearViewport]);

  useEffect(() => {
    const query = window.matchMedia("(prefers-reduced-motion: reduce)");
    const changed = () => { if (query.matches) setRotating(false); };
    query.addEventListener("change", changed);
    return () => query.removeEventListener("change", changed);
  }, []);

  useEffect(() => {
    if (!nearViewport || !host.current) return;
    setUnavailable(false);
    try {
      scene.current = createBodyScene(host.current, {
        weights: [startKg, latestKg, targetKg], heightCm, assets, theme,
        onUnavailable: () => setUnavailable(true),
      });
      scene.current.setSpinning(rotating);
      scene.current.setCloseUp(closeUp);
      scene.current.setHoodie(hoodie);
      scene.current.setGlasses(glasses);
      if (selectedView) scene.current.setView(selectedView);
    } catch {
      setUnavailable(true);
    }
    return () => { scene.current?.dispose(); scene.current = null; };
    // Scene reconstruction is needed only when private geometry arrives.
    // Other changes use the handle below to preserve the camera angle.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [assets, nearViewport]);

  useEffect(() => { scene.current?.setWeights([startKg, latestKg, targetKg], heightCm); }, [startKg, latestKg, targetKg, heightCm]);
  useEffect(() => { scene.current?.setTheme(theme); }, [theme]);
  useEffect(() => { scene.current?.setSpinning(rotating); }, [rotating]);
  useEffect(() => { scene.current?.setCloseUp(closeUp); }, [closeUp]);
  useEffect(() => { scene.current?.setHoodie(hoodie); }, [hoodie]);
  useEffect(() => { scene.current?.setGlasses(glasses); }, [glasses]);

  const selectView = (view: View) => {
    setRotating(false);
    setSelectedView(view);
    scene.current?.setView(view);
  };
  const toggleRotation = () => {
    setSelectedView(null);
    setRotating((current) => !current);
  };

  const figures = [
    { label: "Старт", weight: startKg, detail: "Начало программы" },
    { label: "Сейчас", weight: latestKg, detail: latestKg === null ? "Нет замера" : latestAt ? "Замер " + formatDateTime(latestAt, true) : "Дата замера неизвестна" },
    { label: "Цель", weight: targetKg, detail: "Целевой вес" },
  ];

  return <section className="panel body-model" aria-label="Модель тела">
    <div className="panel__head body-model__header">
      <div><span className="eyebrow">Визуализация изменений</span><h2>Модель тела</h2><p>Три состояния при росте {formatNumber(heightCm, 0)} см</p></div>
      <div className="body-model__toolbar" role="toolbar" aria-label="Управление моделью">
        <button className="button button--secondary" type="button" onClick={toggleRotation} aria-pressed={rotating}>{rotating ? "Остановить вращение" : "Вращать модели"}</button>
        <div className="body-model__view-group" role="group" aria-label="Ракурс">
          {([ ["front", "Анфас"], ["side", "Профиль"], ["back", "Спина"] ] as const).map(([view, label]) =>
            <button key={view} className="button button--secondary" type="button" onClick={() => selectView(view)} aria-pressed={selectedView === view}>{label}</button>)}
        </div>
        <button className="button button--secondary" type="button" onClick={() => setCloseUp((value) => !value)} aria-pressed={closeUp}>Крупно</button>
        <button className="button button--secondary" type="button" onClick={() => setHoodie((value) => !value)} aria-pressed={hoodie}>Худи</button>
        <button className="button button--secondary" type="button" onClick={() => setGlasses((value) => !value)} aria-pressed={glasses}>Очки</button>
      </div>
    </div>
    <div className="body-model__stage" onPointerDown={() => setSelectedView(null)}>
      <div className="body-model__canvas" ref={host} role="img" aria-label="Три 3D-фигуры: старт, сейчас и цель" />
      {unavailable && <p className="body-model__fallback">3D недоступно в этом браузере. Весовые значения указаны ниже.</p>}
      {!unavailable && <p className="body-model__hint">Потяните, чтобы повернуть</p>}
    </div>
    <div className="body-model__labels" aria-label="Веса фигур">{figures.map((figure) =>
      <div className="body-model__label" key={figure.label}>
        <strong>{figure.label}</strong>
        <span className="body-model__label-weight">{figure.weight === null ? "Нет замера" : weightLabel(figure.weight)}</span>
        <small>{figure.detail}</small>
      </div>)}</div>
    <p className="body-model__note">Это иллюстрация по росту и весу. Пропорции тела и изменения лица не являются медицинским прогнозом.</p>
  </section>;
}
