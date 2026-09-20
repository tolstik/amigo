import { useEffect, useRef, useState } from "react";
import * as THREE from "three";
import { chartPalettes } from "../charts/theme";
import { useTheme } from "../theme/ThemeProvider";
import { formatDateTime, formatKg, formatNumber } from "../lib/format";

/** A stylized, skin-toned mannequin, never an estimate of the person's actual body shape. */
function mannequin(bmi: number, skinMaterial: THREE.Material, underwearMaterial: THREE.Material, face: THREE.Texture | null) {
  const body = new THREE.Group();
  const fullness = Math.max(0, Math.min(1.7, (bmi - 20) / 23));
  const ellipsoid = (x: number, y: number, z: number, sx: number, sy: number, sz: number, angle = 0) => {
    const mesh = new THREE.Mesh(new THREE.SphereGeometry(1, 32, 24), skinMaterial);
    mesh.position.set(x, y, z);
    mesh.scale.set(sx, sy, sz);
    mesh.rotation.z = angle;
    body.add(mesh);
    return mesh;
  };
  // One continuous lathed torso gives a readable waist, abdomen and shoulders.
  const outline = [
    [0, .69], [.13 + fullness * .04, .70], [.20 + fullness * .06, .78],
    [.20 + fullness * .09, .89], [.18 + fullness * .12, 1.01],
    [.20 + fullness * .10, 1.15], [.24 + fullness * .06, 1.29],
    [.25 + fullness * .035, 1.36], [.18, 1.40], [.075, 1.43], [0, 1.44],
  ];
  const curve = new THREE.SplineCurve(outline.map(([radius, y]) => new THREE.Vector2(radius, y)));
  const torso = new THREE.Mesh(new THREE.LatheGeometry(curve.getPoints(64), 48), skinMaterial);
  torso.scale.z = .67 + fullness * .18;
  body.add(torso);
  // A short neck and rounded ears make the head read as part of the body.
  ellipsoid(0, 1.48, 0, .067, .10, .067);
  const head = ellipsoid(0, 1.61, 0, .112 + fullness * .006, .145, .105);
  if (face) {
    // Keep the whole head skin-toned and use a shallow, lit curved shell for
    // the photo. The shell follows the same contour instead of looking like a
    // flat sticker pasted in front of the face.
    const faceMaterial = new THREE.MeshStandardMaterial({
      map: face,
      color: "#d2a187",
      transparent: true,
      opacity: .97,
      roughness: .92,
      metalness: 0,
      depthWrite: true,
      polygonOffset: true,
      polygonOffsetFactor: -1,
      side: THREE.FrontSide,
    });
    // SphereGeometry's horizontal range 0..π is the hemisphere facing the
    // camera (+Z); centering the shell this way keeps the whole portrait on
    // the front of the head rather than on its side.
    const faceMesh = new THREE.Mesh(new THREE.SphereGeometry(1, 48, 32, 0, Math.PI, 0, Math.PI), faceMaterial);
    faceMesh.position.copy(head.position);
    faceMesh.position.z += head.scale.z * .018;
    faceMesh.scale.copy(head.scale).multiplyScalar(1.004);
    faceMesh.renderOrder = 2;
    body.add(faceMesh);
  } else ellipsoid(0, 1.59, .098, .025, .033, .024); // Nose makes the rotation easy to see.
  for (const side of [-1, 1]) ellipsoid(side * .108, 1.61, 0, .018, .034, .024);
  // A cloth brief gives the model a human silhouette while leaving the legs
  // and the changing torso volume visible.
  const briefs = new THREE.Mesh(new THREE.SphereGeometry(1, 40, 24), underwearMaterial);
  briefs.position.set(0, .655, .008);
  briefs.scale.set(.235 + fullness * .035, .16 + fullness * .02, .17 + fullness * .025);
  body.add(briefs);
  const waistband = new THREE.Mesh(new THREE.TorusGeometry(.205 + fullness * .03, .014, 10, 40), underwearMaterial);
  waistband.rotation.x = Math.PI / 2;
  waistband.position.set(0, .735, 0);
  body.add(waistband);
  for (const side of [-1, 1]) {
    const armX = .28 + fullness * .055;
    ellipsoid(side * armX, 1.24, 0, .075 + fullness * .025, .19, .072 + fullness * .022, side * .16);
    ellipsoid(side * (armX + .042), .99, .018, .055 + fullness * .018, .17, .056 + fullness * .015, side * .13);
    ellipsoid(side * (armX + .06), .80, .02, .047, .077, .037);
    ellipsoid(side * .114, .59, 0, .093 + fullness * .026, .25, .10 + fullness * .025, side * -.025);
    ellipsoid(side * .12, .25, 0, .065 + fullness * .014, .205, .069 + fullness * .018);
    ellipsoid(side * .12, .055, .055, .068, .046, .123);
  }
  return body;
}

function BodyCanvas({ bmi, rotating }: { bmi: number; rotating: boolean }) {
  const host = useRef<HTMLDivElement>(null);
  const [unavailable, setUnavailable] = useState(false);
  const [face, setFace] = useState<THREE.Texture | null>(null);
  useEffect(() => {
    const controller = new AbortController();
    let texture: THREE.Texture | null = null;
    let objectUrl: string | null = null;
    const basePath = import.meta.env.BASE_URL.replace(/\/$/, "");
    fetch(`${basePath}/api/v1/profile/body-face`, { credentials: "same-origin", cache: "no-store", signal: controller.signal })
      .then(async (response) => {
        if (!response.ok || !response.headers.get("Content-Type")?.startsWith("image/png")) return;
        const blob = await response.blob();
        if (controller.signal.aborted || blob.size > 512 * 1024) return;
        objectUrl = URL.createObjectURL(blob);
        new THREE.TextureLoader().load(objectUrl, (loaded) => {
          if (objectUrl) URL.revokeObjectURL(objectUrl);
          if (controller.signal.aborted) { loaded.dispose(); return; }
          loaded.colorSpace = THREE.SRGBColorSpace;
          texture = loaded;
          setFace(loaded);
        }, undefined, () => { if (objectUrl) URL.revokeObjectURL(objectUrl); });
      }).catch(() => { /* The mannequin remains available without a face texture. */ });
    return () => { controller.abort(); texture?.dispose(); if (objectUrl) URL.revokeObjectURL(objectUrl); };
  }, []);
  const { theme } = useTheme();
  useEffect(() => {
    const element = host.current;
    if (!element) return;
    let renderer: THREE.WebGLRenderer;
    try { renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true }); }
    catch { setUnavailable(true); return; }
    const scene = new THREE.Scene();
    const camera = new THREE.PerspectiveCamera(32, 1, .1, 30);
    camera.position.set(0, 1.02, 3.8);
    camera.lookAt(0, .90, 0);
    renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
    renderer.setClearColor(0, 0);
    element.appendChild(renderer.domElement);
    const skinMaterial = new THREE.MeshStandardMaterial({ color: "#c98f73", roughness: .82, metalness: 0 });
    const underwearMaterial = new THREE.MeshStandardMaterial({ color: "#304d6b", roughness: .72, metalness: .03 });
    const body = mannequin(bmi, skinMaterial, underwearMaterial, face);
    // Face the user on the first frame; rotation then reveals the profile and
    // keeps the personalised texture readable before animation starts.
    body.rotation.y = 0;
    scene.add(body, new THREE.HemisphereLight(0xffffff, 0x526477, 2));
    const key = new THREE.DirectionalLight(0xffffff, 3);
    key.position.set(-3, 4, 4);
    const rim = new THREE.DirectionalLight(0xb4d9ff, 2);
    rim.position.set(3, 2, -2);
    scene.add(key, rim);
    const baseGeometry = new THREE.CylinderGeometry(.43, .46, .025, 64);
    const baseMaterial = new THREE.MeshStandardMaterial({ color: chartPalettes[theme].muted, transparent: true, opacity: .2 });
    const base = new THREE.Mesh(baseGeometry, baseMaterial);
    base.position.y = -.006;
    scene.add(base);
    const render = () => renderer.render(scene, camera);
    const resize = new ResizeObserver(() => {
      const width = element.clientWidth, height = element.clientHeight;
      if (!width || !height) return;
      renderer.setSize(width, height);
      camera.aspect = width / height;
      camera.updateProjectionMatrix();
      render();
    });
    resize.observe(element);
    let frame = 0, previous = 0, visible = true;
    const animate = (time: number) => {
      body.rotation.y += previous ? Math.min(time - previous, 50) * .00058 : 0;
      body.position.y = Math.sin(time * .0018) * .008;
      body.rotation.z = Math.sin(time * .00125) * .006;
      previous = time;
      render();
      frame = requestAnimationFrame(animate);
    };
    const update = () => {
      cancelAnimationFrame(frame);
      previous = 0;
      if (rotating && visible && document.visibilityState === "visible") frame = requestAnimationFrame(animate);
      else render();
    };
    const intersection = new IntersectionObserver(([entry]) => { visible = entry.isIntersecting; update(); });
    intersection.observe(element);
    document.addEventListener("visibilitychange", update);
    const contextLost = (event: Event) => { event.preventDefault(); cancelAnimationFrame(frame); setUnavailable(true); };
    renderer.domElement.addEventListener("webglcontextlost", contextLost);
    update();
    return () => {
      cancelAnimationFrame(frame);
      resize.disconnect(); intersection.disconnect();
      document.removeEventListener("visibilitychange", update);
      renderer.domElement.removeEventListener("webglcontextlost", contextLost);
      body.traverse((object) => {
        if (object instanceof THREE.Mesh) {
          object.geometry.dispose();
          if (object.material !== skinMaterial && object.material !== underwearMaterial) (object.material as THREE.Material).dispose();
        }
      });
      skinMaterial.dispose(); underwearMaterial.dispose(); baseGeometry.dispose(); baseMaterial.dispose(); renderer.dispose();
      renderer.domElement.remove();
    };
  }, [bmi, rotating, theme, face]);
  return <div className="body-model__canvas" ref={host} role="img" aria-label={`Условная трёхмерная фигура при ИМТ ${formatNumber(bmi, 1)}`}>{unavailable && <p className="body-model__fallback">3D недоступно в этом браузере. Вес и ИМТ доступны рядом.</p>}</div>;
}

export default function BodyModel({ latestKg, latestAt, heightCm, startKg, targetKg }: { latestKg: number | null; latestAt: string | null; heightCm: number; startKg: number; targetKg: number }) {
  const [selectedKg, setSelectedKg] = useState<number | null>(null);
  const [rotating, setRotating] = useState(() => !window.matchMedia("(prefers-reduced-motion: reduce)").matches);
  const weight = selectedKg ?? latestKg;
  useEffect(() => {
    const query = window.matchMedia("(prefers-reduced-motion: reduce)");
    const changed = () => { if (query.matches) setRotating(false); };
    query.addEventListener("change", changed);
    return () => query.removeEventListener("change", changed);
  }, []);
  if (weight === null || heightCm <= 0) return null;
  const bmi = weight / (heightCm / 100) ** 2;
  const min = Math.floor(Math.min(targetKg, weight));
  const max = Math.ceil(Math.max(startKg, latestKg ?? startKg, weight));
  return <section className="panel body-model">
    <div className="panel__head"><div><span className="eyebrow">Визуализация изменений</span><h2>Модель тела</h2><p>Условная 3D-фигура по весу и росту</p></div><button className="button button--secondary" type="button" onClick={() => setRotating(!rotating)} aria-pressed={rotating}>{rotating ? "Остановить вращение" : "Вращать модель"}</button></div>
    <div className="body-model__layout">
      <BodyCanvas bmi={bmi} rotating={rotating} />
      <div className="body-model__controls">
        <span className="eyebrow">{selectedKg === null ? "Последний замер" : "Выбранный сценарий"}</span>
        <strong className="body-model__weight">{formatKg(weight, 1)}</strong>
        <p>ИМТ {formatNumber(bmi, 2)} · рост {formatNumber(heightCm, 0)} см</p>
        {selectedKg === null && latestAt && <small>{formatDateTime(latestAt)}</small>}
        <label htmlFor="body-weight">Посмотреть фигуру при другом весе</label>
        <input id="body-weight" type="range" min={min} max={max} step="0.1" value={weight} onChange={(event) => setSelectedKg(Number(event.target.value))} aria-valuetext={formatKg(weight, 1)} />
        <div className="body-model__range"><span>{formatKg(min)}</span><span>{formatKg(max)}</span></div>
        <div className="body-model__presets"><button className="button button--secondary" onClick={() => setSelectedKg(startKg)}>Старт</button><button className="button button--primary" disabled={latestKg === null} onClick={() => setSelectedKg(null)}>Сейчас</button><button className="button button--secondary" onClick={() => setSelectedKg(targetKg)}>Цель</button></div>
        <p className="body-model__note">Это иллюстрация, а не прогноз внешности. Распределение жира, мышечная масса и реальные пропорции по весу и росту не определяются.</p>
      </div>
    </div>
  </section>;
}
