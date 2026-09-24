import { useEffect, useRef, useState } from "react";
import * as THREE from "three";
import { chartPalettes } from "../charts/theme";
import { useTheme } from "../theme/ThemeProvider";
import { formatKg, formatNumber } from "../lib/format";

type FigureSpec = { label: string; weight: number; valueLabel?: string };

function clamp(value: number, min: number, max: number): number {
  return Math.min(max, Math.max(min, value));
}

/** A detailed human-shaped illustration whose only inputs are height and weight. */
function humanFigure(weightKg: number, heightCm: number, skinMaterial: THREE.Material, underwearMaterial: THREE.Material, face: THREE.Texture | null) {
  const body = new THREE.Group();
  const bmi = weightKg / (heightCm / 100) ** 2;
  const fullness = clamp((bmi - 18.5) / 22, 0, 1.25);
  const heightScale = clamp(heightCm / 176, 0.9, 1.1);
  const ellipsoid = (x: number, y: number, z: number, sx: number, sy: number, sz: number, angle = 0) => {
    const mesh = new THREE.Mesh(new THREE.SphereGeometry(1, 24, 18), skinMaterial);
    mesh.position.set(x, y, z);
    mesh.scale.set(sx, sy, sz);
    mesh.rotation.z = angle;
    body.add(mesh);
    return mesh;
  };
  const outline = [
    [0, 0.83], [0.21 + fullness * 0.045, 0.84], [0.26 + fullness * 0.085, 1.02],
    [0.22 + fullness * 0.11, 1.25], [0.24 + fullness * 0.095, 1.50],
    [0.33 + fullness * 0.08, 1.76], [0.30 + fullness * 0.05, 1.97],
    [0.19, 2.08], [0.08, 2.13], [0, 2.14],
  ];
  const curve = new THREE.SplineCurve(outline.map(([radius, y]) => new THREE.Vector2(radius, y)));
  const torso = new THREE.Mesh(new THREE.LatheGeometry(curve.getPoints(64), 40), skinMaterial);
  torso.scale.set(1, 1, 0.73 + fullness * 0.12);
  body.add(torso);
  const shoulder = 0.32 + fullness * 0.08;
  const arm = 0.075 + fullness * 0.025;
  for (const side of [-1, 1]) {
    ellipsoid(side * shoulder, 1.86, 0, arm, 0.24, arm * 0.92, side * 0.08);
    ellipsoid(side * (shoulder + 0.055), 1.56, 0.01, arm * 0.9, 0.22, arm * 0.84, side * 0.1);
    ellipsoid(side * (shoulder + 0.08), 1.29, 0.018, arm * 0.76, 0.11, arm * 0.68);
    ellipsoid(side * 0.135, 0.67, 0, 0.12 + fullness * 0.025, 0.25, 0.105 + fullness * 0.02, side * -0.025);
    ellipsoid(side * 0.135, 0.29, 0, 0.078 + fullness * 0.018, 0.28, 0.078 + fullness * 0.02);
    ellipsoid(side * 0.135, 0.035, 0.075, 0.084, 0.055, 0.16);
  }
  const briefs = new THREE.Mesh(new THREE.SphereGeometry(1, 32, 20), underwearMaterial);
  briefs.position.set(0, 0.79, 0.015);
  briefs.scale.set(0.245 + fullness * 0.06, 0.18 + fullness * 0.025, 0.18 + fullness * 0.035);
  body.add(briefs);
  const waistband = new THREE.Mesh(new THREE.TorusGeometry(0.215 + fullness * 0.045, 0.014, 8, 32), underwearMaterial);
  waistband.rotation.x = Math.PI / 2;
  waistband.position.y = 0.86;
  body.add(waistband);
  ellipsoid(0, 2.19, 0, 0.08, 0.11, 0.075);
  const head = ellipsoid(0, 2.39, 0, 0.135 + fullness * 0.008, 0.17, 0.12);
  for (const side of [-1, 1]) ellipsoid(side * 0.13, 2.4, 0, 0.022, 0.044, 0.028);
  if (face) {
    const faceMaterial = new THREE.MeshStandardMaterial({ map: face, color: "#d2a187", transparent: true, opacity: 0.98, roughness: 0.9, polygonOffset: true, polygonOffsetFactor: -1 });
    const faceMesh = new THREE.Mesh(new THREE.SphereGeometry(1, 40, 28, 0, Math.PI, 0, Math.PI), faceMaterial);
    faceMesh.position.copy(head.position);
    faceMesh.position.z += head.scale.z * 0.018;
    faceMesh.scale.copy(head.scale).multiplyScalar(1.006);
    faceMesh.renderOrder = 2;
    body.add(faceMesh);
  } else {
    ellipsoid(0, 2.37, 0.115, 0.026, 0.036, 0.025);
    for (const side of [-1, 1]) ellipsoid(side * 0.052, 2.43, 0.111, 0.012, 0.012, 0.009);
  }
  body.scale.y = heightScale;
  return body;
}

function bodyFloat(bodies: THREE.Group[], time: number) {
  bodies.forEach((body, index) => {
    body.position.y = Math.sin(time * 0.0017 + index * 0.5) * 0.008;
    body.rotation.z = Math.sin(time * 0.0012 + index * 0.4) * 0.004;
  });
}

function BodyCanvas({ figures, rotating, heightCm }: { figures: FigureSpec[]; rotating: boolean; heightCm: number }) {
  const host = useRef<HTMLDivElement>(null);
  const [unavailable, setUnavailable] = useState(false);
  const [face, setFace] = useState<THREE.Texture | null>(null);
  const { theme } = useTheme();
  useEffect(() => {
    const controller = new AbortController();
    let objectUrl: string | null = null;
    let loadedTexture: THREE.Texture | null = null;
    const basePath = import.meta.env.BASE_URL.replace(/\/$/, "");
    fetch(`${basePath}/api/v1/profile/body-face`, { credentials: "same-origin", cache: "no-store", signal: controller.signal })
      .then(async (response) => {
        if (!response.ok || !response.headers.get("Content-Type")?.startsWith("image/png")) return;
        const blob = await response.blob();
        if (controller.signal.aborted || blob.size > 512 * 1024) return;
        objectUrl = URL.createObjectURL(blob);
        new THREE.TextureLoader().load(objectUrl, (texture) => {
          if (objectUrl) URL.revokeObjectURL(objectUrl);
          if (controller.signal.aborted) { texture.dispose(); return; }
          texture.colorSpace = THREE.SRGBColorSpace;
          loadedTexture = texture;
          setFace(texture);
        }, undefined, () => { if (objectUrl) URL.revokeObjectURL(objectUrl); });
      })
      .catch(() => { /* The neutral facial model remains available without a private texture. */ });
    return () => { controller.abort(); loadedTexture?.dispose(); if (objectUrl) URL.revokeObjectURL(objectUrl); };
  }, []);
  useEffect(() => {
    const element = host.current;
    if (!element) return;
    let renderer: THREE.WebGLRenderer;
    try { renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true }); }
    catch { setUnavailable(true); return; }
    const scene = new THREE.Scene();
    const camera = new THREE.PerspectiveCamera(28, 1, 0.1, 30);
    camera.position.set(0, 1.4, 7.1);
    camera.lookAt(0, 1.25, 0);
    renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
    renderer.setClearColor(0, 0);
    element.appendChild(renderer.domElement);
    const skinMaterial = new THREE.MeshStandardMaterial({ color: "#c98f73", roughness: 0.8, metalness: 0 });
    const underwearMaterial = new THREE.MeshStandardMaterial({ color: "#304d6b", roughness: 0.72, metalness: 0.03 });
    const bodies = figures.map((figure, index) => {
      const body = humanFigure(figure.weight, heightCm, skinMaterial, underwearMaterial, face);
      body.position.x = (index - 1) * 1.28;
      scene.add(body);
      return body;
    });
    scene.add(new THREE.HemisphereLight(0xffffff, 0x526477, 2));
    const key = new THREE.DirectionalLight(0xffffff, 3); key.position.set(-3, 4, 4);
    const rim = new THREE.DirectionalLight(0xb4d9ff, 2); rim.position.set(3, 2, -2);
    scene.add(key, rim);
    const baseMaterial = new THREE.MeshStandardMaterial({ color: chartPalettes[theme].muted, transparent: true, opacity: 0.12 });
    const base = new THREE.Mesh(new THREE.CylinderGeometry(2.45, 2.55, 0.025, 64), baseMaterial);
    base.position.y = -0.01;
    scene.add(base);
    const render = () => renderer.render(scene, camera);
    const resize = new ResizeObserver(() => {
      const width = element.clientWidth; const height = element.clientHeight;
      if (!width || !height) return;
      renderer.setSize(width, height); camera.aspect = width / height; camera.updateProjectionMatrix(); render();
    });
    resize.observe(element);
    let frame = 0; let previous = 0; let visible = true;
    const animate = (time: number) => {
      const elapsed = previous ? Math.min(time - previous, 50) : 0;
      bodies.forEach((body) => { body.rotation.y += elapsed * 0.00042; });
      previous = time; bodyFloat(bodies, time); render(); frame = requestAnimationFrame(animate);
    };
    const update = () => {
      cancelAnimationFrame(frame); previous = 0;
      if (rotating && visible && document.visibilityState === "visible") frame = requestAnimationFrame(animate); else render();
    };
    const intersection = new IntersectionObserver(([entry]) => { visible = entry.isIntersecting; update(); });
    intersection.observe(element); document.addEventListener("visibilitychange", update);
    const contextLost = (event: Event) => { event.preventDefault(); cancelAnimationFrame(frame); setUnavailable(true); };
    renderer.domElement.addEventListener("webglcontextlost", contextLost); update();
    return () => {
      cancelAnimationFrame(frame); resize.disconnect(); intersection.disconnect(); document.removeEventListener("visibilitychange", update);
      renderer.domElement.removeEventListener("webglcontextlost", contextLost);
      bodies.forEach((body) => body.traverse((object) => {
        if (object instanceof THREE.Mesh) { object.geometry.dispose(); if (object.material !== skinMaterial && object.material !== underwearMaterial) (object.material as THREE.Material).dispose(); }
      }));
      skinMaterial.dispose(); underwearMaterial.dispose(); base.geometry.dispose(); baseMaterial.dispose(); renderer.dispose(); renderer.domElement.remove();
    };
  }, [figures, face, heightCm, rotating, theme]);
  return <div className="body-model__canvas" ref={host} role="img" aria-label="Три человекоподобные фигуры: старт, сейчас и цель">{unavailable && <p className="body-model__fallback">3D недоступно в этом браузере. Весовые значения доступны в подписях.</p>}</div>;
}

export default function BodyModel({ latestKg, heightCm, startKg, targetKg }: { latestKg: number | null; heightCm: number; startKg: number; targetKg: number }) {
  const [rotating, setRotating] = useState(() => !window.matchMedia("(prefers-reduced-motion: reduce)").matches);
  useEffect(() => {
    const query = window.matchMedia("(prefers-reduced-motion: reduce)");
    const changed = () => { if (query.matches) setRotating(false); };
    query.addEventListener("change", changed);
    return () => query.removeEventListener("change", changed);
  }, []);
  const currentWeight = latestKg ?? startKg;
  const figures: FigureSpec[] = [
    { label: "Старт", weight: startKg },
    { label: "Сейчас", weight: currentWeight, valueLabel: latestKg === null ? "Нет замера" : undefined },
    { label: "Цель", weight: targetKg },
  ];
  return <section className="panel body-model">
    <div className="panel__head"><div><span className="eyebrow">Визуализация изменений</span><h2>Модель тела</h2><p>Три состояния по весу и росту {formatNumber(heightCm, 0)} см</p></div><button className="button button--secondary" type="button" onClick={() => setRotating(!rotating)} aria-pressed={rotating}>{rotating ? "Остановить вращение" : "Вращать модели"}</button></div>
    <div className="body-model__figures">
      <BodyCanvas figures={figures} rotating={rotating} heightCm={heightCm} />
      <div className="body-model__labels" aria-hidden="true">{figures.map((figure) => <div className="body-model__label" key={figure.label}><strong>{figure.label}</strong><span>{figure.valueLabel ?? formatKg(figure.weight, 1)}</span></div>)}</div>
    </div>
    <p className="body-model__note">Это визуальная иллюстрация по росту и весу. Пропорции тела и изменения лица не являются медицинским прогнозом.</p>
  </section>;
}
