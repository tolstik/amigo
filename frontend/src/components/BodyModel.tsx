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
    const mesh = new THREE.Mesh(new THREE.SphereGeometry(1, 32, 24), skinMaterial);
    mesh.position.set(x, y, z);
    mesh.scale.set(sx, sy, sz);
    mesh.rotation.z = angle;
    body.add(mesh);
    return mesh;
  };
  const taperedLimb = (from: THREE.Vector3, to: THREE.Vector3, upper: number, lower: number) => {
    const direction = new THREE.Vector3().subVectors(to, from);
    const mesh = new THREE.Mesh(new THREE.CylinderGeometry(upper, lower, direction.length(), 32, 3), skinMaterial);
    mesh.position.copy(from).add(to).multiplyScalar(0.5);
    mesh.quaternion.setFromUnitVectors(new THREE.Vector3(0, 1, 0), direction.normalize());
    body.add(mesh);
  };
  const outline = [
    [0, 0.79], [0.18 + fullness * 0.075, 0.82], [0.22 + fullness * 0.12, 1.02],
    [0.20 + fullness * 0.18, 1.26], [0.22 + fullness * 0.13, 1.48],
    [0.29 + fullness * 0.085, 1.74], [0.32 + fullness * 0.075, 1.91],
    [0.23 + fullness * 0.035, 2.04], [0.10, 2.11], [0, 2.13],
  ];
  const curve = new THREE.SplineCurve(outline.map(([radius, y]) => new THREE.Vector2(radius, y)));
  const torso = new THREE.Mesh(new THREE.LatheGeometry(curve.getPoints(64), 40), skinMaterial);
  torso.scale.set(1, 1, 0.78 + fullness * 0.17);
  body.add(torso);
  const shoulder = 0.37 + fullness * 0.075;
  const arm = 0.075 + fullness * 0.03;
  for (const side of [-1, 1]) {
    const shoulderPoint = new THREE.Vector3(side * shoulder, 1.94, 0);
    const elbow = new THREE.Vector3(side * (shoulder + 0.08), 1.52, 0.015);
    const wrist = new THREE.Vector3(side * (shoulder + 0.095), 1.16, 0.035);
    ellipsoid(shoulderPoint.x, shoulderPoint.y, 0, arm * 1.25, arm * 1.4, arm * 1.2);
    taperedLimb(shoulderPoint, elbow, arm * 1.13, arm * 0.8);
    ellipsoid(elbow.x, elbow.y, elbow.z, arm * 0.82, arm * 0.8, arm * 0.82);
    taperedLimb(elbow, wrist, arm * 0.85, arm * 0.62);
    ellipsoid(wrist.x, wrist.y - 0.08, wrist.z, arm * 0.7, 0.12, arm * 0.46);
    ellipsoid(wrist.x + side * 0.049, wrist.y - 0.04, wrist.z + 0.035, 0.026, 0.065, 0.026, side * -0.28);
    const hip = new THREE.Vector3(side * (0.15 + fullness * 0.022), 0.84, 0);
    const knee = new THREE.Vector3(side * 0.16, 0.43, 0.012);
    const ankle = new THREE.Vector3(side * 0.17, 0.075, 0.025);
    ellipsoid(hip.x, 0.73, 0, 0.13 + fullness * 0.043, 0.24, 0.12 + fullness * 0.03);
    taperedLimb(hip, knee, 0.115 + fullness * 0.035, 0.085 + fullness * 0.014);
    ellipsoid(knee.x, knee.y, knee.z, 0.086 + fullness * 0.014, 0.095, 0.085);
    taperedLimb(knee, ankle, 0.09 + fullness * 0.012, 0.063 + fullness * 0.008);
    ellipsoid(ankle.x, 0.045, 0.105, 0.088, 0.055, 0.17);
  }
  const briefs = new THREE.Mesh(new THREE.SphereGeometry(1, 32, 20), underwearMaterial);
  briefs.position.set(0, 0.81, 0.015);
  briefs.scale.set(0.245 + fullness * 0.09, 0.17 + fullness * 0.025, 0.18 + fullness * 0.06);
  body.add(briefs);
  const waistband = new THREE.Mesh(new THREE.TorusGeometry(0.215 + fullness * 0.045, 0.014, 8, 32), underwearMaterial);
  waistband.rotation.x = Math.PI / 2;
  waistband.position.y = 0.86;
  body.add(waistband);
  ellipsoid(0, 2.2, 0, 0.075, 0.12, 0.07);
  const head = ellipsoid(0, 2.43, 0, 0.168 + fullness * 0.01, 0.215, 0.145);
  ellipsoid(0, 2.345, 0.025, 0.14, 0.13, 0.13);
  for (const side of [-1, 1]) ellipsoid(side * 0.167, 2.41, 0, 0.026, 0.049, 0.032);
  const hair = new THREE.Mesh(new THREE.SphereGeometry(1, 40, 28, 0, Math.PI * 2, 0, Math.PI * 0.44), new THREE.MeshStandardMaterial({ color: "#302b2b", roughness: 0.93 }));
  hair.position.copy(head.position);
  hair.scale.set(head.scale.x * 1.01, head.scale.y * 1.03, head.scale.z * 1.02);
  body.add(hair);
  if (face) {
    const faceMaterial = new THREE.MeshStandardMaterial({ map: face, color: "#d2a187", transparent: true, opacity: 0.98, roughness: 0.9, polygonOffset: true, polygonOffsetFactor: -1 });
    const faceMesh = new THREE.Mesh(new THREE.SphereGeometry(1, 40, 28, 0, Math.PI, 0, Math.PI), faceMaterial);
    faceMesh.position.copy(head.position);
    faceMesh.position.z += head.scale.z * 0.018;
    faceMesh.scale.copy(head.scale).multiplyScalar(1.006);
    faceMesh.renderOrder = 2;
    body.add(faceMesh);
  } else {
    const eyeMaterial = new THREE.MeshStandardMaterial({ color: "#2d2b2b", roughness: 0.45 });
    const lipMaterial = new THREE.MeshStandardMaterial({ color: "#8b5d54", roughness: 0.9 });
    for (const side of [-1, 1]) {
      const eye = new THREE.Mesh(new THREE.SphereGeometry(1, 16, 12), eyeMaterial);
      eye.position.set(side * 0.067, 2.475, 0.131);
      eye.scale.set(0.014, 0.009, 0.006);
      body.add(eye);
      ellipsoid(side * 0.05, 2.375, 0.129, 0.057, 0.065, 0.022);
    }
    ellipsoid(0, 2.42, 0.146, 0.026, 0.064, 0.034);
    const mouth = new THREE.Mesh(new THREE.SphereGeometry(1, 16, 10), lipMaterial);
    mouth.position.set(0, 2.335, 0.148);
    mouth.scale.set(0.049, 0.008, 0.006);
    body.add(mouth);
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
