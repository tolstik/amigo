// @ts-nocheck — the source scene uses heterogeneous procedural geometry records.
// Geometry, clothing, accessories, and lighting are adapted from the user-provided Claude scene.
// Private head/ear meshes and photo textures are supplied only at runtime.
import * as THREE from 'three';
import { RoomEnvironment } from 'three/addons/environments/RoomEnvironment.js';
import { RoundedBoxGeometry } from 'three/addons/geometries/RoundedBoxGeometry.js';
import type { ThemeName } from '../theme/ThemeProvider';

export type BodySceneView = 'front' | 'side' | 'back';
export type BodySceneWeights = [number, number | null, number];
export interface BodyModelAssets {
  version: 1;
  head: { nr: number; seg: number; q: number; pos: string; skin: [number, number, number]; anchors: unknown; crown: number; bottom: number; tex: string };
  ear: { pos: number[]; uv: number[]; idx: number[]; top: [number, number, number]; tex: string };
}
export interface BodySceneOptions {
  weights: BodySceneWeights;
  heightCm: number;
  assets: BodyModelAssets | null;
  theme: ThemeName;
  onUnavailable?: () => void;
}
export interface BodySceneHandle {
  setWeights(weights: BodySceneWeights, heightCm?: number): void;
  setTheme(theme: ThemeName): void;
  setSpinning(enabled: boolean): void;
  setView(view: BodySceneView): void;
  setCloseUp(enabled: boolean): void;
  setHoodie(enabled: boolean): void;
  setGlasses(enabled: boolean): void;
  render(): void;
  dispose(): void;
}

const inertHandle = (): BodySceneHandle => ({
  setWeights() {}, setTheme() {}, setSpinning() {}, setView() {}, setCloseUp() {},
  setHoodie() {}, setGlasses() {}, render() {}, dispose() {},
});

export function createBodyScene(host: HTMLElement, options: BodySceneOptions): BodySceneHandle {
  let renderer: THREE.WebGLRenderer;
  try {
    renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true });
  } catch {
    options.onUnavailable?.();
    return inertHandle();
  }
  const canvas = renderer.domElement;
  canvas.style.width = '100%';
  canvas.style.height = '100%';
  canvas.style.touchAction = 'pan-y';
  host.appendChild(canvas);
  let requestRender = () => {};
  const privateAssets = options.assets?.version === 1 && options.assets.head && options.assets.ear ? options.assets : null;
  const HD = privateAssets?.head ?? null;
  const ED = privateAssets?.ear ?? null;
  const HEIGHT = 1.76;
  const W_LEAN = 76, W_HEAVY = 127, W_PHOTO = 118;
const TAU = Math.PI * 2;
const lerp = (a, b, t) => a + (b - a) * t;
const clamp = (x, a, b) => Math.max(a, Math.min(b, x));
const smooth = (e0, e1, x) => { const t = clamp((x - e0) / (e1 - e0), 0, 1); return t * t * (3 - 2 * t); };
const V3 = (x = 0, y = 0, z = 0) => new THREE.Vector3(x, y, z);
const F_PHOTO = (W_PHOTO - W_LEAN) / (W_HEAVY - W_LEAN);
const HEAD_SCALE = 0.95;

/* ───────────── interpolation ───────────── */
function catmull(p0, p1, p2, p3, t) {
  const t2 = t * t, t3 = t2 * t;
  return 0.5 * (2 * p1 + (-p0 + p2) * t + (2 * p0 - 5 * p1 + 4 * p2 - p3) * t2 + (-p0 + 3 * p1 - 3 * p2 + p3) * t3);
}
function makeSampler(keys, field = 'y') {
  const names = Object.keys(keys[0]).filter(k => k !== field);
  const n = keys.length;
  return (x) => {
    let k = 0;
    while (k < n - 2 && x > keys[k + 1][field]) k++;
    const a = keys[k], b = keys[k + 1];
    const t = clamp((x - a[field]) / (b[field] - a[field]), 0, 1);
    const p0 = keys[Math.max(k - 1, 0)], p3 = keys[Math.min(k + 2, n - 1)];
    const o = { [field]: x };
    for (const nm of names) o[nm] = catmull(p0[nm], a[nm], b[nm], p3[nm], t);
    return o;
  };
}

/* ───────────── loft geometry ─────────────
   ring: { c, R, F, a, df, db, nf, nb, t0, t1, mat, u, v, off(th), shade(th) } */
function sePoint(r, th) {
  const c = Math.cos(th), s = Math.sin(th);
  const n = s >= 0 ? (r.nf ?? 2) : (r.nb ?? 2);
  let px = r.a * Math.sign(c) * Math.pow(Math.abs(c), 2 / n);
  let pz = (s >= 0 ? r.df : r.db) * Math.sign(s) * Math.pow(Math.abs(s), 2 / n);
  if (r.off) { const k = r.off(th); const l = Math.hypot(px, pz) || 1; px += px / l * k; pz += pz / l * k; }
  return [r.c.x + r.R.x * px + r.F.x * pz, r.c.y + r.R.y * px + r.F.y * pz, r.c.z + r.R.z * px + r.F.z * pz];
}
function loft(rings, segs, closed = true) {
  const nR = rings.length, nV = segs + 1;
  const pos = new Float32Array(nR * nV * 3), uv = new Float32Array(nR * nV * 2);
  const shaded = rings.some(r => r.shade);
  const col = shaded ? new Float32Array(nR * nV * 3) : null;
  for (let i = 0; i < nR; i++) {
    const r = rings[i];
    const t0 = r.t0 ?? 0, t1 = r.t1 ?? TAU;
    for (let j = 0; j < nV; j++) {
      const th = t0 + (t1 - t0) * j / segs;
      const p = sePoint(r, th);
      const k = (i * nV + j) * 3;
      pos[k] = p[0]; pos[k + 1] = p[1]; pos[k + 2] = p[2];
      uv[(i * nV + j) * 2] = (j / segs) * (r.u ?? 1);
      uv[(i * nV + j) * 2 + 1] = r.v ?? i / (nR - 1);
      if (col) { const g = r.shade ? r.shade(th) : 1; col[k] = g; col[k + 1] = g; col[k + 2] = g; }
    }
  }
  const idx = [], groups = [];
  let cur = null;
  for (let i = 0; i < nR - 1; i++) {
    const m = rings[i + 1].mat ?? 0;
    if (!cur || cur.m !== m) { cur = { start: idx.length, count: 0, m }; groups.push(cur); }
    for (let j = 0; j < segs; j++) {
      const a = i * nV + j, b = (i + 1) * nV + j, c = i * nV + j + 1, d = (i + 1) * nV + j + 1;
      idx.push(a, b, c, c, b, d);
    }
    cur.count = idx.length - cur.start;
  }
  const g = new THREE.BufferGeometry();
  g.setAttribute('position', new THREE.BufferAttribute(pos, 3));
  g.setAttribute('uv', new THREE.BufferAttribute(uv, 2));
  if (col) g.setAttribute('color', new THREE.BufferAttribute(col, 3));
  g.setIndex(idx);
  groups.forEach(gr => g.addGroup(gr.start, gr.count, gr.m));
  g.computeVertexNormals();
  if (closed) {
    const nrm = g.attributes.normal;
    for (let i = 0; i < nR; i++) {
      const a = i * nV, b = i * nV + segs;
      const x = nrm.getX(a) + nrm.getX(b), y = nrm.getY(a) + nrm.getY(b), z = nrm.getZ(a) + nrm.getZ(b);
      const l = Math.hypot(x, y, z) || 1;
      nrm.setXYZ(a, x / l, y / l, z / l); nrm.setXYZ(b, x / l, y / l, z / l);
    }
  }
  return g;
}
const AXIS_X = V3(1, 0, 0), AXIS_Z = V3(0, 0, 1);
function frame(U) {
  const F = AXIS_Z.clone().addScaledVector(U, -U.dot(AXIS_Z)).normalize();
  const R = new THREE.Vector3().crossVectors(U, F).normalize();
  return { R, F };
}
function ellPerim(a, b) { return Math.PI * (3 * (a + b) - Math.sqrt((3 * a + b) * (a + 3 * b))); }
/* limb loft along a curve; prof(s) → {a, df, db, n, off, shade}; s=0 top, 1 bottom */
function limb(curve, s0, s1, steps, prof, segs, opts = {}) {
  const rings = [];
  const tile = opts.tile ?? 0.1;
  let arc = 0, prev = null;
  for (let i = steps; i >= 0; i--) {
    const s = lerp(s0, s1, i / steps);
    const c = curve.getPointAt(s);
    const U = curve.getTangentAt(s).negate();
    const { R, F } = frame(U);
    const p = prof(s);
    if (prev) arc += c.distanceTo(prev);
    prev = c;
    const circ = ellPerim(p.a, (p.df + p.db) / 2);
    rings.push({ c, R, F, a: p.a, df: p.df, db: p.db, nf: p.n ?? 2, nb: p.n ?? 2, mat: p.mat ?? 0, off: p.off, shade: p.shade,
      u: opts.u ?? Math.max(1, Math.round(circ / tile)), v: arc / tile });
  }
  return loft(rings, segs, true);
}
function ribbon(points, normals, width, vTile) {
  const n = points.length, pos = new Float32Array(n * 2 * 3), uv = new Float32Array(n * 2 * 2), idx = [];
  let arc = 0;
  for (let i = 0; i < n; i++) {
    const p = points[i], nn = normals[i];
    const t = points[Math.min(i + 1, n - 1)].clone().sub(points[Math.max(i - 1, 0)]).normalize();
    const side = new THREE.Vector3().crossVectors(t, nn).normalize().multiplyScalar(width / 2);
    if (i) arc += p.distanceTo(points[i - 1]);
    const a = p.clone().add(side), b = p.clone().sub(side);
    pos.set([a.x, a.y, a.z, b.x, b.y, b.z], i * 6);
    uv.set([0, arc / vTile, 1, arc / vTile], i * 4);
    if (i < n - 1) { const k = i * 2; idx.push(k, k + 1, k + 2, k + 2, k + 1, k + 3); }
  }
  const g = new THREE.BufferGeometry();
  g.setAttribute('position', new THREE.BufferAttribute(pos, 3));
  g.setAttribute('uv', new THREE.BufferAttribute(uv, 2));
  g.setIndex(idx); g.computeVertexNormals();
  return g;
}

/* ───────────── textures ───────────── */
function rng(seed) { return () => { seed |= 0; seed = seed + 0x6D2B79F5 | 0; let t = Math.imul(seed ^ seed >>> 15, 1 | seed); t = t + Math.imul(t ^ t >>> 7, 61 | t) ^ t; return ((t ^ t >>> 14) >>> 0) / 4294967296; }; }
function canvasTex(size, draw, srgb = true, h = size) {
  const cv = document.createElement('canvas'); cv.width = size; cv.height = h;
  draw(cv.getContext('2d'), size, h);
  const t = new THREE.CanvasTexture(cv);
  t.wrapS = t.wrapT = THREE.RepeatWrapping;
  if (srgb) t.colorSpace = THREE.SRGBColorSpace;
  t.anisotropy = 8;
  return t;
}
function heightToNormal(ctx, S, hf, strength) {
  const img = ctx.createImageData(S, S), d = img.data;
  const at = (x, y) => hf[((y + S) % S) * S + ((x + S) % S)];
  for (let y = 0; y < S; y++) for (let x = 0; x < S; x++) {
    const dx = (at(x + 1, y) - at(x - 1, y)) * strength, dy = (at(x, y + 1) - at(x, y - 1)) * strength;
    const l = Math.hypot(dx, dy, 1), k = (y * S + x) * 4;
    d[k] = (-dx / l * 0.5 + 0.5) * 255; d[k + 1] = (dy / l * 0.5 + 0.5) * 255; d[k + 2] = (1 / l * 0.5 + 0.5) * 255; d[k + 3] = 255;
  }
  ctx.putImageData(img, 0, 0);
}
const fishTex = canvasTex(512, (ctx, S) => {
  ctx.fillStyle = '#1b2434'; ctx.fillRect(0, 0, S, S);
  const r = rng(7), pts = [];
  let guard = 0;
  while (pts.length < 13 && guard++ < 4000) {
    const p = { x: r() * S, y: r() * S, a: r() * TAU };
    if (pts.every(q => { let dx = Math.abs(p.x - q.x), dy = Math.abs(p.y - q.y); dx = Math.min(dx, S - dx); dy = Math.min(dy, S - dy); return dx * dx + dy * dy > 118 * 118; })) pts.push(p);
  }
  const L = 96, Hh = L * 0.2;
  const fish = (x, y, a) => {
    ctx.save(); ctx.translate(x, y); ctx.rotate(a);
    ctx.fillStyle = '#d3dbe5';
    ctx.beginPath(); ctx.moveTo(-L * 0.5, 0);
    ctx.quadraticCurveTo(-L * 0.05, -Hh * 1.9, L * 0.32, 0);
    ctx.quadraticCurveTo(-L * 0.05, Hh * 1.9, -L * 0.5, 0); ctx.fill();
    ctx.beginPath(); ctx.moveTo(L * 0.28, 0); ctx.lineTo(L * 0.5, -Hh * 0.9); ctx.lineTo(L * 0.44, 0); ctx.lineTo(L * 0.5, Hh * 0.9); ctx.closePath(); ctx.fill();
    ctx.strokeStyle = '#1b2434'; ctx.lineWidth = 3.2;
    ctx.beginPath(); ctx.moveTo(-L * 0.3, 0); ctx.lineTo(L * 0.22, 0); ctx.stroke();
    ctx.lineWidth = 1.6;
    for (let i = -2; i <= 3; i++) { const xx = i * L * 0.075; ctx.beginPath(); ctx.moveTo(xx - 3, -4); ctx.lineTo(xx + 3, 4); ctx.stroke(); }
    ctx.fillStyle = '#1b2434'; ctx.beginPath(); ctx.arc(-L * 0.36, -Hh * 0.28, 3.2, 0, TAU); ctx.fill();
    ctx.restore();
  };
  for (const p of pts) for (const ox of [-S, 0, S]) for (const oy of [-S, 0, S]) fish(p.x + ox, p.y + oy, p.a);
  ctx.fillStyle = '#b8c3d1';
  for (let i = 0; i < 70; i++) { const x = r() * S, y = r() * S; ctx.beginPath(); ctx.arc(x, y, 1.6 + r() * 1.4, 0, TAU); ctx.fill(); }
});
/* denim: indigo twill with white weft showing through, slubs */
const denimTex = canvasTex(256, (ctx, S) => {
  const r = rng(11), img = ctx.createImageData(S, S), d = img.data;
  const slub = new Float32Array(S); for (let y = 0; y < S; y++) slub[y] = (r() - 0.5) * 16;
  for (let y = 0; y < S; y++) for (let x = 0; x < S; x++) {
    const tw = ((x + y) % 4) < 2 ? 1 : 0;           // 2/2 twill diagonal
    const n = (r() - 0.5) * 22 + slub[y] * 0.7 + slub[x] * 0.3;
    const base = tw ? [52, 72, 108] : [96, 116, 150];
    const k = (y * S + x) * 4;
    d[k] = clamp(base[0] + n, 0, 255); d[k + 1] = clamp(base[1] + n, 0, 255); d[k + 2] = clamp(base[2] + n * 1.1, 0, 255); d[k + 3] = 255;
  }
  ctx.putImageData(img, 0, 0);
});
const denimNormal = canvasTex(256, (ctx, S) => {
  const hf = new Float32Array(S * S);
  for (let y = 0; y < S; y++) for (let x = 0; x < S; x++) hf[y * S + x] = ((x + y) % 4) < 2 ? 1 : 0;
  heightToNormal(ctx, S, hf, 0.6);
}, false);
const knitNormal = canvasTex(256, (ctx, S) => {
  const r = rng(5), hf = new Float32Array(S * S);
  for (let y = 0; y < S; y++) for (let x = 0; x < S; x++) {
    const v = Math.sin(x * TAU / 8) * 0.5 + Math.sin((y + (Math.floor(x / 4) % 2) * 2) * TAU / 6) * 0.35;
    hf[y * S + x] = v + (r() - 0.5) * 0.8;
  }
  heightToNormal(ctx, S, hf, 0.9);
}, false);
const teethTex = canvasTex(32, (ctx, S, H) => {
  ctx.fillStyle = '#1c2230'; ctx.fillRect(0, 0, S, H);
  ctx.fillStyle = '#8f959e';
  ctx.fillRect(3, 2, 15, H / 2 - 4); ctx.fillRect(14, H / 2 + 2, 15, H / 2 - 4);
}, true, 32);
const lensTex = canvasTex(64, (ctx, S) => {
  const g = ctx.createLinearGradient(0, 0, 0, S);
  g.addColorStop(0, '#15171b'); g.addColorStop(0.55, '#262a31'); g.addColorStop(1, '#4a525c');
  ctx.fillStyle = g; ctx.fillRect(0, 0, S, S);
});
const watchFaceTex = canvasTex(256, (ctx, S) => {
  ctx.fillStyle = '#030405'; ctx.fillRect(0, 0, S, S);
  ctx.translate(S / 2, S / 2);
  for (let i = 0; i < 60; i++) {
    ctx.save(); ctx.rotate(i / 60 * TAU);
    ctx.fillStyle = i % 5 ? 'rgba(160,170,180,.35)' : 'rgba(230,235,240,.8)';
    ctx.fillRect(-1.5, -S * 0.42, 3, i % 5 ? 8 : 18); ctx.restore();
  }
  const hand = (ang, len, w, c) => { ctx.save(); ctx.rotate(ang); ctx.fillStyle = c; ctx.fillRect(-w / 2, -len, w, len + 10); ctx.restore(); };
  hand(TAU * 10.15 / 12, S * 0.24, 7, '#e9edf2'); hand(TAU * 0.17, S * 0.34, 5, '#e9edf2'); hand(TAU * 0.62, S * 0.36, 2, '#f39b3a');
  ctx.fillStyle = '#f39b3a'; ctx.beginPath(); ctx.arc(0, 0, 5, 0, TAU); ctx.fill();
});
const headTex = HD ? new THREE.TextureLoader().load(HD.tex, () => requestRender()) : null;
if (headTex) { headTex.colorSpace = THREE.SRGBColorSpace; headTex.anisotropy = 8; }
const earTex = ED ? new THREE.TextureLoader().load(ED.tex, () => requestRender()) : null;
if (earTex) earTex.colorSpace = THREE.SRGBColorSpace;

/* ───────────── materials ───────────── */
const skinCol = HD ? new THREE.Color().setRGB(HD.skin[0] / 255 * 1.04, HD.skin[1] / 255 * 1.04, HD.skin[2] / 255 * 1.04, THREE.SRGBColorSpace) : new THREE.Color(0xc49679);
const M = {
  head: new THREE.MeshStandardMaterial({ map: headTex, emissiveMap: headTex, emissive: new THREE.Color(0x6a6a6a), roughness: 0.6, envMapIntensity: 0.3 }),
  ear: new THREE.MeshStandardMaterial({ map: earTex, emissiveMap: earTex, emissive: new THREE.Color(0x4a4a4a), roughness: 0.6, side: THREE.DoubleSide, envMapIntensity: 0.3 }),
  skin: new THREE.MeshPhysicalMaterial({ color: skinCol, roughness: 0.55, sheen: 0.25, sheenRoughness: 0.6, sheenColor: new THREE.Color(0xffc4a4), envMapIntensity: 0.45 }),
  nail: new THREE.MeshStandardMaterial({ color: 0xe8c3b3, roughness: 0.3, envMapIntensity: 0.6 }),
  tee: new THREE.MeshStandardMaterial({ map: fishTex, roughness: 0.92, side: THREE.DoubleSide, envMapIntensity: 0.35 }),
  teePlain: new THREE.MeshStandardMaterial({ color: 0x1b2434, roughness: 0.92, side: THREE.DoubleSide, envMapIntensity: 0.35 }),
  jeans: new THREE.MeshStandardMaterial({ map: denimTex, normalMap: denimNormal, normalScale: new THREE.Vector2(0.35, 0.35), vertexColors: true, roughness: 0.92, envMapIntensity: 0.35 }),
  jeansPlain: new THREE.MeshStandardMaterial({ map: denimTex, normalMap: denimNormal, normalScale: new THREE.Vector2(0.35, 0.35), roughness: 0.92, side: THREE.DoubleSide, envMapIntensity: 0.35 }),
  seam: new THREE.MeshStandardMaterial({ color: 0x33445f, roughness: 0.9 }),
  stitch: new THREE.MeshStandardMaterial({ color: 0xc08b43, roughness: 0.7 }),
  hoodie: new THREE.MeshStandardMaterial({ color: 0x1f2739, roughness: 0.96, normalMap: knitNormal, normalScale: new THREE.Vector2(0.22, 0.22), side: THREE.DoubleSide, envMapIntensity: 0.3 }),
  hoodieDark: new THREE.MeshStandardMaterial({ color: 0x161c2a, roughness: 0.96, side: THREE.DoubleSide, envMapIntensity: 0.3 }),
  teeth: new THREE.MeshStandardMaterial({ map: teethTex, metalness: 0.75, roughness: 0.35, side: THREE.DoubleSide, envMapIntensity: 1 }),
  zip: new THREE.MeshStandardMaterial({ color: 0xa9aeb6, metalness: 0.9, roughness: 0.3, envMapIntensity: 1 }),
  cord: new THREE.MeshStandardMaterial({ color: 0x2a3348, roughness: 0.8 }),
  watch: new THREE.MeshStandardMaterial({ color: 0x1a1b1e, metalness: 0.8, roughness: 0.32, envMapIntensity: 0.9 }),
  screen: new THREE.MeshPhysicalMaterial({ map: watchFaceTex, emissiveMap: watchFaceTex, emissive: new THREE.Color(0x333333), color: 0xffffff, metalness: 0.1, roughness: 0.05, clearcoat: 1, clearcoatRoughness: 0.02, envMapIntensity: 1.2 }),
  lens: new THREE.MeshPhysicalMaterial({ map: lensTex, metalness: 0.25, roughness: 0.03, clearcoat: 1, clearcoatRoughness: 0.02, transparent: true, opacity: 0.92, side: THREE.DoubleSide, envMapIntensity: 1.8 }),
  frame: new THREE.MeshStandardMaterial({ color: 0x7a7d82, metalness: 1, roughness: 0.22, envMapIntensity: 1.3 }),
  pad: new THREE.MeshPhysicalMaterial({ color: 0xdddddd, roughness: 0.1, transparent: true, opacity: 0.55 }),
  tip: new THREE.MeshStandardMaterial({ color: 0x1c1d20, roughness: 0.45 }),
  shoe: new THREE.MeshStandardMaterial({ color: 0x2a2d32, roughness: 0.75, envMapIntensity: 0.4 }),
  sole: new THREE.MeshStandardMaterial({ color: 0xdcd8cf, roughness: 0.9, envMapIntensity: 0.3 }),
};

/* ───────────── body tables ───────────── */
const TORSO = [
  [0.790, 0.122, 0.155, 0.060, 0.088, 0.070, 0.100],
  [0.820, 0.155, 0.200, 0.083, 0.120, 0.100, 0.140],
  [0.870, 0.170, 0.224, 0.090, 0.160, 0.118, 0.160],
  [0.930, 0.165, 0.232, 0.094, 0.210, 0.112, 0.154],
  [0.990, 0.153, 0.238, 0.097, 0.248, 0.097, 0.142],
  [1.050, 0.148, 0.238, 0.100, 0.260, 0.090, 0.136],
  [1.120, 0.150, 0.234, 0.104, 0.246, 0.090, 0.136],
  [1.190, 0.157, 0.230, 0.110, 0.216, 0.094, 0.140],
  [1.270, 0.171, 0.230, 0.118, 0.184, 0.102, 0.146],
  [1.340, 0.181, 0.228, 0.113, 0.152, 0.104, 0.140],
  [1.405, 0.186, 0.222, 0.094, 0.118, 0.095, 0.122],
  [1.450, 0.176, 0.206, 0.066, 0.083, 0.074, 0.095],
  [1.480, 0.108, 0.134, 0.054, 0.069, 0.061, 0.079],
  [1.510, 0.058, 0.078, 0.049, 0.063, 0.054, 0.069],
];

/* ───────────── photo head (decoded once) ───────────── */
const HEAD_BASE = (() => {
  if (!HD) return null;
  const bin = atob(HD.pos), n = bin.length / 2, out = new Float32Array(n);
  for (let i = 0; i < n; i++) { let v = bin.charCodeAt(2 * i) | (bin.charCodeAt(2 * i + 1) << 8); if (v > 32767) v -= 65536; out[i] = v / 32767 * HD.q; }
  return out;
})();
const HEAD_INDEX = (() => {
  if (!HD) return null;
  const nR = HD.nr, nV = HD.seg + 1, idx = [];
  for (let i = 0; i < nR - 1; i++) for (let j = 0; j < HD.seg; j++) {
    const a = i * nV + j, b = (i + 1) * nV + j, c = i * nV + j + 1, d = (i + 1) * nV + j + 1;
    idx.push(a, b, c, c, b, d);
  }
  return idx;
})();
const HEAD_UV = (() => {
  if (!HD) return null;
  const nR = HD.nr, nV = HD.seg + 1, uv = new Float32Array(nR * nV * 2);
  for (let i = 0; i < nR; i++) for (let j = 0; j < nV; j++) { uv[(i * nV + j) * 2] = j / HD.seg; uv[(i * nV + j) * 2 + 1] = i / (nR - 1); }
  return uv;
})();
function headWidthScale(y, fr) { return 1 + fr * (0.03 + 0.085 * smooth(0.0, -0.10, y)); }
function buildHeadGeo(f) {
  const fr = f - F_PHOTO, pos = new Float32Array(HEAD_BASE.length);
  for (let i = 0; i < pos.length; i += 3) {
    let x = HEAD_BASE[i], y = HEAD_BASE[i + 1], z = HEAD_BASE[i + 2];
    x *= headWidthScale(y, fr);
    const g = smooth(-0.075, -0.135, y);
    if (g > 0) { y -= fr * 0.009 * g; if (z > -0.08) z += fr * 0.004 * g; }
    const ch = smooth(-0.02, -0.07, y) * (1 - smooth(-0.09, -0.12, y));
    if (z > -0.02) z += fr * 0.003 * ch;
    pos[i] = x; pos[i + 1] = y; pos[i + 2] = z;
  }
  const g = new THREE.BufferGeometry();
  g.setAttribute('position', new THREE.BufferAttribute(pos, 3));
  g.setAttribute('uv', new THREE.BufferAttribute(HEAD_UV, 2));
  g.setIndex(HEAD_INDEX);
  g.computeVertexNormals();
  const nrm = g.attributes.normal, nV = HD.seg + 1, nR = HD.nr;
  for (let i = 0; i < nR; i++) {
    const a = i * nV, b = a + HD.seg;
    const x = nrm.getX(a) + nrm.getX(b), y = nrm.getY(a) + nrm.getY(b), z = nrm.getZ(a) + nrm.getZ(b), l = Math.hypot(x, y, z) || 1;
    nrm.setXYZ(a, x / l, y / l, z / l); nrm.setXYZ(b, x / l, y / l, z / l);
  }
  for (let j = 0; j < nV; j++) nrm.setXYZ((nR - 1) * nV + j, 0, 1, 0);
  return g;
}
const EAR_GEO = (() => {
  if (!ED) return null;
  const g = new THREE.BufferGeometry();
  g.setAttribute('position', new THREE.Float32BufferAttribute(ED.pos, 3));
  g.setAttribute('uv', new THREE.Float32BufferAttribute(ED.uv, 2));
  g.setIndex(ED.idx); g.computeVertexNormals(); g.userData.shared = true;
  return g;
})();
const EAR_Y = (() => { if (!ED) return 0; let s = 0; for (let i = 1; i < ED.pos.length; i += 3) s += ED.pos[i]; return s / (ED.pos.length / 3); })();

/* ───────────── sunglasses (aviators) in head-frame metres ───────────── */
const GLASSES = (() => {
  const g = new THREE.Group();
  const P2 = [[-0.026, 0.019], [0.0, 0.021], [0.025, 0.019], [0.031, 0.009], [0.029, -0.007], [0.019, -0.022], [0.005, -0.029], [-0.009, -0.027], [-0.021, -0.017], [-0.028, -0.003], [-0.029, 0.011]];
  const zL = 0.027, LX = 0.0345, LY = -0.006, BEND = 3.5;
  const bend = (x, y) => -BEND * (x * x + y * y);
  const curve = new THREE.CatmullRomCurve3(P2.map(p => V3(p[0], p[1], bend(p[0], p[1]))), true, 'centripetal');
  const shape = new THREE.Shape(curve.getPoints(80).map(p => new THREE.Vector2(p.x, p.y)));
  const lensG = new THREE.ShapeGeometry(shape, 24);
  { const pa = lensG.attributes.position, uv = lensG.attributes.uv;
    for (let i = 0; i < pa.count; i++) { const x = pa.getX(i), y = pa.getY(i); pa.setZ(i, bend(x, y)); uv.setXY(i, (x + 0.031) / 0.062, (y + 0.03) / 0.052); }
    lensG.computeVertexNormals(); }
  const rimG = new THREE.TubeGeometry(curve, 120, 0.00085, 6, true);
  for (const sx of [-1, 1]) {
    const lg = new THREE.Group();
    lg.position.set(sx * LX, LY, zL); lg.rotation.set(-0.06, sx * 0.14, 0); lg.scale.x = sx;
    lg.add(new THREE.Mesh(lensG, M.lens), new THREE.Mesh(rimG, M.frame));
    g.add(lg);
    const hinge = V3(sx * (LX + 0.031), LY + 0.012, zL - 0.006);
    const end = new THREE.Mesh(new THREE.BoxGeometry(0.004, 0.0035, 0.007), M.frame); end.position.copy(hinge); g.add(end);
    const temple = new THREE.CatmullRomCurve3([hinge, V3(sx * 0.079, 0.0105, -0.018), V3(sx * 0.0845, 0.0125, -0.058), V3(sx * 0.0835, 0.0135, -0.077), V3(sx * 0.080, 0.002, -0.091), V3(sx * 0.076, -0.018, -0.097)]);
    g.add(new THREE.Mesh(new THREE.TubeGeometry(temple, 60, 0.00105, 6), M.frame));
    const tipC = new THREE.CatmullRomCurve3(temple.getPoints(60).slice(44));
    g.add(new THREE.Mesh(new THREE.TubeGeometry(tipC, 20, 0.0017, 8), M.tip));
    // nose pad on an arm
    const padAt = V3(sx * 0.0098, LY - 0.004, zL - 0.011);
    const arm = new THREE.CatmullRomCurve3([V3(sx * (LX - 0.024), LY + 0.004, zL - 0.001), V3(sx * 0.016, LY + 0.0, zL - 0.008), padAt]);
    g.add(new THREE.Mesh(new THREE.TubeGeometry(arm, 12, 0.0006, 5), M.frame));
    const pad = new THREE.Mesh(new THREE.SphereGeometry(1, 12, 8), M.pad);
    pad.scale.set(0.0022, 0.0055, 0.0038); pad.position.copy(padAt); pad.rotation.y = sx * 0.6; g.add(pad);
  }
  const top = new THREE.CatmullRomCurve3([V3(-LX + 0.021, LY + 0.0195, zL + 0.0005), V3(0, LY + 0.0215, zL + 0.002), V3(LX - 0.021, LY + 0.0195, zL + 0.0005)]);
  const brg = new THREE.CatmullRomCurve3([V3(-LX + 0.027, LY + 0.007, zL), V3(-0.006, LY + 0.0125, zL + 0.002), V3(0.006, LY + 0.0125, zL + 0.002), V3(LX - 0.027, LY + 0.007, zL)]);
  g.add(new THREE.Mesh(new THREE.TubeGeometry(top, 16, 0.0009, 6), M.frame), new THREE.Mesh(new THREE.TubeGeometry(brg, 16, 0.0009, 6), M.frame));
  g.traverse(o => { if (o.isMesh) { o.geometry.userData.shared = true; o.castShadow = true; } });
  return g;
})();

/* ───────────── hands ───────────── */
function buildHand(fc, rw, outSign) {
  const g = new THREE.Group();
  const tS = 1 + 0.14 * fc, wS = 1 + 0.06 * fc;
  const pk = [[-0.108, 0.004, 0.018, -0.003], [-0.103, 0.0085, 0.033, -0.002], [-0.092, 0.0125, 0.0415, -0.001], [-0.07, 0.0145, 0.0435, -0.002],
              [-0.04, 0.0155, 0.041, -0.001], [-0.012, 0.0165, 0.036, 0]];
  const palm = pk.map(([y, a, d, cx]) => ({ c: V3(cx, y, 0.002), R: AXIS_X, F: AXIS_Z, a: a * tS, df: d * wS, db: d * wS, nf: 2.5, nb: 2.5 }));
  palm.push({ c: V3(0, 0.008, 0), R: AXIS_X, F: AXIS_Z, a: rw * 0.74, df: rw * 1.16, db: rw * 1.14, nf: 2, nb: 2 });
  const add = (geo, mat) => { const m = new THREE.Mesh(geo, mat); m.castShadow = true; g.add(m); return m; };
  add(loft(palm, 28), M.skin);
  const thenar = add(new THREE.SphereGeometry(1, 16, 12), M.skin); thenar.scale.set(0.0105 * tS, 0.026, 0.013 * wS); thenar.position.set(-0.006, -0.045, 0.026 * wS);
  const FING = [
    [0.0255, -0.094, [0.045, 0.026, 0.022], 0.0092, 0.0070, 0.03, [8, 18, 10]],
    [0.0085, -0.098, [0.049, 0.030, 0.023], 0.0096, 0.0072, 0.0, [11, 22, 12]],
    [-0.0085, -0.095, [0.046, 0.028, 0.022], 0.0090, 0.0068, -0.025, [14, 26, 13]],
    [-0.0245, -0.089, [0.037, 0.021, 0.019], 0.0080, 0.0060, -0.06, [17, 30, 15]],
  ];
  const rS = 1 + 0.16 * fc;
  const finger = (pts, r0, r1, nailAt) => {
    const curve = new THREE.CatmullRomCurve3(pts, false, 'centripetal');
    const L = curve.getLength();
    add(limb(curve, 0, 1, 26, s => {
      const l = s * L;
      let r = lerp(r0, r1, s) * rS;
      const tipR = r1 * rS;
      if (l > L - tipR) r *= Math.sqrt(Math.max(0.02, 1 - Math.pow((l - (L - tipR)) / tipR, 2)));
      return { a: r * 0.9, df: r, db: r };
    }, 14), M.skin);
    if (nailAt) {
      const { p, dir, r } = nailAt;
      const dors = V3(-dir.y, dir.x, 0).normalize();
      const nail = new THREE.Mesh(new THREE.SphereGeometry(1, 12, 8), M.nail);
      nail.scale.set(0.0014, r * 1.3, r * 0.82);
      nail.matrixAutoUpdate = false;
      nail.matrix.makeBasis(dors, dir, AXIS_Z).setPosition(p.clone().addScaledVector(dors, r * 0.78));
      nail.matrix.multiply(new THREE.Matrix4().makeScale(0.0014, r * 1.25, r * 0.8));
      g.add(nail);
    }
  };
  for (const [z, y0, lens, r0, r1, spread, flex] of FING) {
    let dir = V3(0, -1, 0).applyAxisAngle(AXIS_X, -spread).normalize();
    const p0 = V3(-0.002, y0, z * wS);
    const pts = [p0.clone().add(V3(0, 0.014, 0)), p0.clone()];
    let p = p0.clone(), last = null;
    flex.forEach((deg, k) => {
      dir = dir.clone().applyAxisAngle(AXIS_Z, -deg * Math.PI / 180);
      const q = p.clone().addScaledVector(dir, lens[k]);
      if (k === 2) last = { p: p.clone().addScaledVector(dir, lens[k] * 0.55), dir: dir.clone(), r: r1 * rS };
      pts.push(q); p = q;
    });
    finger(pts, r0, r1, last);
  }
  const T = [V3(-0.004, -0.026, 0.028 * wS), V3(-0.009, -0.052, 0.046 * wS), V3(-0.014, -0.076, 0.053 * wS), V3(-0.019, -0.095, 0.051 * wS), V3(-0.022, -0.108, 0.047 * wS)];
  const tl = T[4].clone().sub(T[3]).normalize();
  finger(T, 0.0112, 0.0082, { p: T[3].clone().lerp(T[4], 0.35), dir: tl, r: 0.0082 * rS });
  return g;
}

/* ───────────── figure builder ───────────── */
function buildFigure(weight) {
  const f = clamp((weight - W_LEAN) / (W_HEAVY - W_LEAN), -0.5, 1.6);
  const fc = clamp(f, 0, 1);
  const mix = (l, h) => Math.max(lerp(l, h, f), l * 0.55);
  const fig = new THREE.Group();
  const hoodieParts = [], glassesParts = [], teeParts = [];
  const add = (geo, mat, parent = fig) => { const m = new THREE.Mesh(geo, mat); m.castShadow = true; m.receiveShadow = true; parent.add(m); return m; };

  /* torso */
  const torso = makeSampler(TORSO.map(r => ({ y: r[0], a: mix(r[1], r[2]), df: mix(r[3], r[4]), db: mix(r[5], r[6]) })));
  const NF = lerp(2.45, 2.1, fc), NB = lerp(2.55, 2.35, fc);
  const nAt = (y, n) => y > 1.44 ? lerp(n, 2.0, clamp((y - 1.44) / 0.07, 0, 1)) : n;
  const tRing = (y, off = 0) => { const p = torso(y); return { c: V3(0, y, 0), R: AXIS_X, F: AXIS_Z, a: p.a + off, df: p.df + off, db: p.db + off, nf: nAt(y, NF), nb: nAt(y, NB) }; };
  const torsoPt = (th, y, off) => V3(...sePoint(tRing(y, off), th));
  const HEM = 0.875, TILE = 0.22;
  const chest = torso(1.27);
  const uTee = Math.max(3, Math.round(ellPerim(chest.a, (chest.df + chest.db) / 2) / TILE));
  const hipP = torso(0.87), uJeans = Math.max(3, Math.round(ellPerim(hipP.a, (hipP.df + hipP.db) / 2) / 0.1));
  const jeansShadeT = (y) => (th) => 0.94 + 0.26 * Math.pow(Math.max(0, Math.sin(th)), 1.6) * (1 - 0.4 * smooth(0.82, 0.87, y));
  const rings = [];
  const ys = [];
  for (let y = 0.79; y <= 1.5101; y += 0.0075) ys.push(+y.toFixed(4));
  const torsoRing = (y, off, mat) => Object.assign(tRing(y, off), { mat, u: mat ? uTee : uJeans, v: y / (mat ? TILE : 0.1), shade: mat ? null : jeansShadeT(y) });
  for (const y of ys) { if (y < HEM) rings.push(torsoRing(y, 0, 0)); else break; }
  rings.push(torsoRing(HEM, 0, 0));
  for (const y of [HEM, ...ys.filter(y => y > HEM)]) rings.push(torsoRing(y, 0.007 * clamp(1 - (y - HEM) / 0.05, 0, 1), 1));
  rings.forEach(r => { if (!r.shade) r.shade = () => 1; });
  add(loft(rings, 72), [M.jeans, M.tee]);

  /* jeans details on the pelvis: fly, front pocket edges, back pockets */
  const onTorso = (pts, off) => pts.map(([th, y]) => torsoPt(th, y, off));
  const stitchLine = (pts, r = 0.0008) => add(new THREE.TubeGeometry(new THREE.CatmullRomCurve3(pts), Math.max(8, pts.length * 4), r, 5), M.stitch);
  stitchLine(onTorso([[Math.PI / 2 + 0.16, HEM - 0.001], [Math.PI / 2 + 0.16, 0.82], [Math.PI / 2 + 0.1, 0.803], [Math.PI / 2 + 0.02, 0.797]], 0.0012));
  for (const sx of [-1, 1]) {
    const th = (d) => Math.PI / 2 - sx * d;
    stitchLine(onTorso([[th(0.62), HEM - 0.001], [th(0.8), 0.858], [th(1.05), 0.845], [th(1.3), 0.84]], 0.0014));
    // back pocket patch
    const bth = (d) => 3 * Math.PI / 2 + sx * d;
    const P = [[0.28, 0.872], [0.95, 0.872], [0.92, 0.812], [0.62, 0.797], [0.32, 0.812]];
    const grid = [];
    for (let i = 0; i <= 6; i++) for (let j = 0; j <= 8; j++) {
      const v = i / 6, u = j / 8;
      const y = lerp(0.872, 0.799 + 0.012 * Math.abs(u - 0.5) * 2, v);
      const a = lerp(lerp(0.28, 0.95, u), lerp(0.34, 0.9, u), v);
      grid.push(torsoPt(bth(a), y, 0.0022));
    }
    const pg = new THREE.BufferGeometry().setFromPoints(grid);
    const pidx = [];
    for (let i = 0; i < 6; i++) for (let j = 0; j < 8; j++) { const a = i * 9 + j, b = a + 1, c = a + 9, d = c + 1; pidx.push(a, c, b, b, c, d); }
    pg.setIndex(pidx);
    const puv = []; for (let i = 0; i <= 6; i++) for (let j = 0; j <= 8; j++) puv.push(j / 8 * 1.5, i / 6 * 0.75);
    pg.setAttribute('uv', new THREE.Float32BufferAttribute(puv, 2)); pg.computeVertexNormals();
    add(pg, M.jeansPlain);
    stitchLine(onTorso(P.concat([P[0]]).map(([a, y]) => [bth(a), y]), 0.0028), 0.0007);
  }

  /* neck */
  const rN = mix(0.053, 0.066);
  const neckRings = [];
  for (let i = 0; i <= 12; i++) {
    const t = i / 12, y = lerp(1.44, 1.605, t);
    const r = rN * (1 + 0.08 * t);
    neckRings.push({ c: V3(0, y, lerp(-0.014, 0.016, t)), R: AXIS_X, F: AXIS_Z, a: r * 1.06, df: r * 0.95, db: r, nf: 2, nb: 2 });
  }
  add(loft(neckRings, 36), M.skin);
  const collar = new THREE.Mesh(new THREE.TorusGeometry(rN + 0.006, 0.0065, 10, 48), M.teePlain);
  collar.rotation.x = Math.PI / 2; collar.position.set(0, 1.503, -0.002); collar.scale.set(1.05, 1.0, 1);
  collar.castShadow = true; fig.add(collar);

  /* The portrait mesh and atlas arrive only from the authenticated endpoint. */
  const head = new THREE.Group();
  head.scale.setScalar(HEAD_SCALE);
  head.position.set(0, HEIGHT - (HD?.crown ?? 0.13) * HEAD_SCALE, 0.08);
  fig.add(head);
  if (HD && ED) {
    add(buildHeadGeo(f), M.head, head);
    const earShift = (headWidthScale(EAR_Y, f - F_PHOTO) - 1) * 0.08;
    for (const sx of [-1, 1]) {
      const ear = add(EAR_GEO, M.ear, head);
      ear.scale.x = sx; ear.position.x = sx * earShift;
    }
  } else {
    const neutral = add(new THREE.SphereGeometry(0.116, 32, 24), M.skin, head);
    neutral.scale.set(1 + 0.045 * fc, 1.12, 0.9);
    for (const sx of [-1, 1]) {
      const ear = add(new THREE.SphereGeometry(0.023, 16, 12), M.skin, head);
      ear.scale.set(0.72, 1.32, 0.75);
      ear.position.set(sx * 0.114, -0.02, 0);
    }
  }
  const gl = GLASSES.clone(); gl.visible = false;
  if (!HD) { gl.position.z = 0.07; gl.scale.setScalar(1.3); }
  head.add(gl); glassesParts.push(gl);

  /* legs with seams, fading and stacking folds */
  const hx = mix(0.085, 0.118), kx = mix(0.096, 0.128), ax = lerp(0.116, 0.132, fc);
  const legProf = makeSampler([
    { s: 0.0, r: mix(0.074, 0.104) }, { s: 0.12, r: mix(0.082, 0.130) }, { s: 0.3, r: mix(0.074, 0.110) },
    { s: 0.49, r: mix(0.058, 0.080) }, { s: 0.65, r: mix(0.057, 0.075) }, { s: 0.85, r: mix(0.051, 0.064) }, { s: 1.0, r: mix(0.054, 0.065) },
  ], 's');
  for (const sx of [-1, 1]) {
    const curve = new THREE.CatmullRomCurve3([V3(sx * hx, 0.92, 0), V3(sx * kx, 0.5, 0.012), V3(sx * ax, 0.07, -0.004)]);
    const ph = sx * 1.7;
    const legGeo = limb(curve, 0, 1, 110, s => {
      const r = legProf(s).r;
      return { a: r, df: r, db: r * 1.03,
        off: (th) => {
          const st = smooth(0.8, 0.97, s);
          let o = st * (0.0032 * Math.sin(s * 165 + 1.8 * Math.cos(th + ph) + ph) + 0.0014 * Math.sin(th * 3 + s * 70));
          o += Math.exp(-Math.pow((s - 0.515) / 0.045, 2)) * Math.max(0, -Math.sin(th)) * 0.0022 * Math.sin(s * 300 + th);
          o += 0.0008 * Math.sin(th * 5 + s * 37 + ph) * smooth(0.05, 0.4, s) * (1 - st);
          if (s > 0.985) o += 0.0022;
          return o;
        },
        shade: (th) => {
          const fr = Math.max(0, Math.sin(th)), bk = Math.max(0, -Math.sin(th)), outer = Math.cos(th) * sx;
          const thigh = Math.exp(-Math.pow((s - 0.2) / 0.15, 2)), knee = Math.exp(-Math.pow((s - 0.49) / 0.06, 2));
          let g = 0.92 + 0.28 * Math.pow(fr, 1.6) * thigh + 0.16 * Math.pow(fr, 2) * knee - 0.08 * bk * knee - 0.05 * Math.max(0, -outer);
          const wh = smooth(0.0, 0.03, s) * (1 - smooth(0.1, 0.16, s)) * Math.pow(fr, 2) * Math.max(0, -outer + 0.3);
          g += wh * 0.25 * Math.pow(Math.max(0, Math.sin(s * 150 + th * 2)), 6);
          g += 0.1 * smooth(0.95, 1, s);
          return clamp(g, 0.75, 1.4);
        } };
    }, 48, { tile: 0.1 });
    add(legGeo, M.jeans);
    // outer side seam + inseam from the leg's own vertices (j = 0 / 24 are ±x)
    const pa = legGeo.attributes.position, na = legGeo.attributes.normal, nV = 49;
    const seamCol = (j, off) => { const pts = [], nrm = []; for (let i = 0; i <= 110; i += 3) { const k = i * nV + j; const n = V3(na.getX(k), na.getY(k), na.getZ(k)); pts.push(V3(pa.getX(k), pa.getY(k), pa.getZ(k)).addScaledVector(n, off)); nrm.push(n); } return { pts, nrm }; };
    const outerJ = sx > 0 ? 0 : 24, innerJ = sx > 0 ? 24 : 0;
    const os = seamCol(outerJ, 0.0012);
    add(new THREE.TubeGeometry(new THREE.CatmullRomCurve3(os.pts), 120, 0.0016, 5), M.seam);
    const os2 = seamCol(outerJ + (sx > 0 ? 1 : -1), 0.0011);
    add(new THREE.TubeGeometry(new THREE.CatmullRomCurve3(os2.pts), 120, 0.0006, 4), M.stitch);
    const is = seamCol(innerJ, 0.001);
    add(new THREE.TubeGeometry(new THREE.CatmullRomCurve3(is.pts.slice(0, -1)), 120, 0.0012, 5), M.seam);
    // hem fold line
    { const pts = []; for (let j = 0; j <= 48; j += 2) { const k = 110 * 0 + j; pts.push(V3(pa.getX(k), pa.getY(k) + 0.012, pa.getZ(k)).add(V3(na.getX(k), 0, na.getZ(k)).multiplyScalar(0.0014))); }
      add(new THREE.TubeGeometry(new THREE.CatmullRomCurve3(pts, true), 60, 0.0007, 4), M.stitch); }
    // shoe
    const shoe = new THREE.Group(); shoe.position.set(sx * ax, 0, 0.055); shoe.rotation.y = sx * 0.1; fig.add(shoe);
    const bodyG = new THREE.SphereGeometry(1, 32, 18);
    const bp = bodyG.attributes.position;
    for (let i = 0; i < bp.count; i++) if (bp.getY(i) < 0) bp.setY(i, bp.getY(i) * 0.3);
    bodyG.computeVertexNormals();
    const body = add(bodyG, M.shoe, shoe); body.scale.set(0.052, 0.06, 0.135); body.position.set(0, 0.03, 0.0);
    const soleG = new THREE.SphereGeometry(1, 32, 12);
    const ps = soleG.attributes.position;
    for (let i = 0; i < ps.count; i++) if (ps.getY(i) < 0) ps.setY(i, ps.getY(i) * 0.15);
    soleG.computeVertexNormals();
    const sole = add(soleG, M.sole, shoe); sole.scale.set(0.056, 0.02, 0.142); sole.position.set(0, 0.006, 0.0);
  }

  /* arms */
  const hoodOff = 0.013;
  const armProf = makeSampler([
    { s: 0.0, r: mix(0.050, 0.070) }, { s: 0.15, r: mix(0.048, 0.072) }, { s: 0.35, r: mix(0.045, 0.067) },
    { s: 0.56, r: mix(0.038, 0.053) }, { s: 0.7, r: mix(0.040, 0.054) }, { s: 0.88, r: mix(0.031, 0.042) }, { s: 1.0, r: mix(0.028, 0.035) },
  ], 's');
  const sh = torso(1.405);
  for (const sx of [-1, 1]) {
    const S = V3(sx * (sh.a - 0.03), 1.415, -0.006);
    const ex = Math.max(Math.abs(S.x) + 0.01, torso(1.10).a + hoodOff + armProf(0.56).r + 0.018);
    const wx = Math.max(ex - 0.01, torso(0.9).a + hoodOff + armProf(1).r + 0.022);
    const E = V3(sx * ex, 1.095, -0.028), W = V3(sx * wx, 0.852, 0.03);
    const curve = new THREE.CatmullRomCurve3([S, E, W], false, 'centripetal');
    add(limb(curve, 0, 1, 56, s => { const r = armProf(s).r, fl = clamp((s - 0.78) / 0.22, 0, 1); return { a: r * (1 - 0.26 * fl), df: r * (1.02 + 0.14 * fl), db: r * (1 + 0.14 * fl) }; }, 32), M.skin);
    // tee sleeve with hem
    teeParts.push(add(limb(curve, 0, 0.34, 18, s => { const r = armProf(s).r + 0.008 + 0.004 * (s / 0.34) + (s > 0.32 ? 0.0012 : 0); return { a: r, df: r, db: r }; }, 32, { tile: TILE }), M.tee));
    const deltG = new THREE.SphereGeometry(1, 24, 16);
    { const uvA = deltG.attributes.uv; for (let q = 0; q < uvA.count; q++) uvA.setXY(q, uvA.getX(q) * 2, uvA.getY(q)); }
    const delt = add(deltG, M.tee); teeParts.push(delt);
    const dr = armProf(0).r + 0.008; delt.scale.set(dr, dr * 1.05, dr * 1.1); delt.position.copy(S).add(V3(sx * 0.002, -0.004, 0));
    // hoodie sleeve: pushed up, bunched folds, ribbed cuff
    const hs = add(limb(curve, 0, 0.64, 60, s => {
      let r = armProf(s).r + hoodOff;
      const bunch = smooth(0.34, 0.46, s);
      const cuff = smooth(0.6, 0.615, s);
      r += 0.006 * bunch * (1 - cuff);
      return { a: r, df: r * 1.03, db: r, off: (th) => {
        let o = 0.0045 * bunch * (1 - cuff) * Math.sin(s * 110 + 1.7 * Math.cos(th) + sx) + 0.0012 * Math.sin(th * 4 + s * 40);
        o += 0.002 * Math.exp(-Math.pow((s - 0.52) / 0.05, 2)) * Math.max(0, Math.sin(th + 0.5)) * Math.sin(s * 200);
        if (cuff > 0) o += cuff * (0.0009 * Math.cos(th * 56) - 0.004);
        return o;
      } };
    }, 64, { tile: 0.05 }), M.hoodie);
    const hd = add(new THREE.SphereGeometry(1, 24, 16), M.hoodie);
    const hr = armProf(0).r + hoodOff + 0.002; hd.scale.set(hr, hr * 1.05, hr * 1.1); hd.position.copy(S).add(V3(sx * 0.002, -0.004, 0));
    // armhole seam ring
    const s0 = 0.06, c0 = curve.getPointAt(s0), U0 = curve.getTangentAt(s0).negate(), f0 = frame(U0), r0 = armProf(s0).r + hoodOff + 0.0012;
    const ring = new THREE.Mesh(new THREE.TorusGeometry(r0, 0.0012, 5, 40), M.hoodieDark);
    ring.matrixAutoUpdate = false; ring.matrix.makeBasis(f0.R, f0.F, U0).setPosition(c0); ring.scale.set(1, 1, 1); fig.add(ring);
    hoodieParts.push(hs, hd, ring);
    // hand
    const Ut = curve.getTangentAt(1).negate();
    const { R, F } = frame(Ut);
    const out = sx > 0 ? R.clone() : R.clone().negate();
    const hand = buildHand(fc, armProf(1).r, sx);
    hand.matrixAutoUpdate = false;
    hand.matrix.makeBasis(out, Ut, F).setPosition(W);
    fig.add(hand);
    // smartwatch with link bracelet on the left wrist (+x)
    if (sx > 0) {
      const s = 0.925, c = curve.getPointAt(s), U2 = curve.getTangentAt(s).negate(), fr = frame(U2), r = armProf(s).r;
      const fl = clamp((s - 0.78) / 0.22, 0, 1), ra = r * (1 - 0.26 * fl), rd = r * (1.02 + 0.14 * fl);
      const wg = new THREE.Group(); wg.matrixAutoUpdate = false; wg.matrix.makeBasis(fr.R, U2, fr.F).setPosition(c); fig.add(wg);
      const ang0 = Math.atan2(0.42 * rd, 0.9 * ra);
      const nL = Math.round(ellPerim(ra + 0.004, rd + 0.004) / 0.0082);
      const linkG = new RoundedBoxGeometry(0.0038, 0.021, 0.0074, 2, 0.0012);
      for (let i = 0; i < nL; i++) {
        const t = ang0 + (i + 0.5) / nL * TAU;
        if (Math.cos(t - ang0) > 0.82) continue;                   // under the case
        const x = (ra + 0.0026) * Math.cos(t), z = (rd + 0.0026) * Math.sin(t);
        const link = add(linkG, M.watch, wg);
        link.position.set(x, 0, z);
        link.rotation.y = -Math.atan2(Math.sin(t) / rd, Math.cos(t) / ra);
      }
      const dir = V3(Math.cos(ang0), 0, Math.sin(ang0));
      const dist = Math.hypot(ra * Math.cos(ang0), rd * Math.sin(ang0)) + 0.0058;
      const caseG = new RoundedBoxGeometry(0.0108, 0.038, 0.044, 4, 0.0045);
      const kase = add(caseG, M.watch, wg);
      kase.position.copy(dir).multiplyScalar(dist); kase.rotation.y = -ang0;
      const scrG = new RoundedBoxGeometry(0.0016, 0.0335, 0.0395, 3, 0.0007);
      const scr = add(scrG, M.screen, wg);
      scr.position.copy(dir).multiplyScalar(dist + 0.0048); scr.rotation.y = -ang0;
      const crown = add(new THREE.CylinderGeometry(0.0028, 0.0028, 0.004, 16), M.watch, wg);
      crown.position.copy(dir).multiplyScalar(dist + 0.001).add(V3(0, -0.021, 0)).addScaledVector(V3(-Math.sin(ang0), 0, Math.cos(ang0)), 0.009);
    }
  }

  /* hoodie shell: drape folds, ribbed waistband, open front */
  const gap = makeSampler([
    { y: 0.835, g: lerp(0.40, 0.64, fc) }, { y: 1.0, g: lerp(0.36, 0.66, fc) }, { y: 1.2, g: lerp(0.33, 0.55, fc) },
    { y: 1.38, g: lerp(0.36, 0.46, fc) }, { y: 1.47, g: lerp(0.56, 0.62, fc) }, { y: 1.505, g: 0.85 },
  ]);
  const hy = [];
  for (let y = 1.505; y >= 0.8349; y -= 0.005) hy.push(+y.toFixed(4));
  const hrs = [];
  let prevH = null;
  for (const y of hy) {
    const p = torso(y);
    let a = p.a + hoodOff, df = p.df + hoodOff, db = p.db + hoodOff;
    if (prevH && y < 1.37) {
      const k = 0.3 * 0.005;
      a = Math.max(a, prevH.a - k); df = Math.max(df, prevH.df - k * 1.4); db = Math.max(db, prevH.db - k);
    }
    prevH = { a, df, db };
    const g = gap(y).g;
    const rib = smooth(0.888, 0.884, y), gather = smooth(0.884, 0.838, y);
    const drape = smooth(1.3, 0.95, y);
    hrs.push({ c: V3(0, y, 0), R: AXIS_X, F: AXIS_Z, a, df, db, nf: nAt(y, NF), nb: nAt(y, NB), t0: Math.PI / 2 + g, t1: Math.PI / 2 + TAU - g,
      u: Math.round(ellPerim(a, (df + db) / 2) / 0.05), v: y / 0.05,
      off: (th) => 0.0042 * drape * (1 - rib) * Math.sin(th * 7 + 0.8) * (0.6 + 0.4 * Math.sin(th * 3 + y * 20)) + rib * (0.0011 * Math.cos(th * 120) - 0.0065 * gather + 0.0015) });
  }
  hrs.reverse();
  const hoodieGeo = loft(hrs, 96, false);
  hoodieParts.push(add(hoodieGeo, M.hoodie));
  const NVH = 97;
  // zipper teeth ribbons + slider
  const pa = hoodieGeo.attributes.position, na = hoodieGeo.attributes.normal;
  for (const j of [0, 96]) {
    const pts = [], nrm = [];
    for (let i = 0; i < hrs.length; i += 2) {
      const k = i * NVH + j; const n = V3(na.getX(k), na.getY(k), na.getZ(k)).normalize();
      pts.push(V3(pa.getX(k), pa.getY(k), pa.getZ(k)).addScaledVector(n, 0.0022)); nrm.push(n);
    }
    const sm = new THREE.CatmullRomCurve3(pts).getPoints(pts.length * 2);
    const smn = sm.map((_, i) => nrm[Math.min(nrm.length - 1, Math.round(i / 2))]);
    hoodieParts.push(add(ribbon(sm, smn, 0.0062, 0.0045), M.teeth));
  }
  { const k = 96; const base = V3(pa.getX(k), pa.getY(k) + 0.02, pa.getZ(k) + 0.006);
    const slider = add(new RoundedBoxGeometry(0.011, 0.018, 0.006, 2, 0.002), M.zip); slider.position.copy(base);
    const tab = add(new RoundedBoxGeometry(0.008, 0.026, 0.002, 2, 0.0008), M.zip); tab.position.copy(base).add(V3(0, -0.018, 0.004)); tab.rotation.x = 0.25;
    hoodieParts.push(slider, tab); }
  // side welt pockets
  for (const sx of [-1, 1]) {
    const th = (d) => Math.PI / 2 - sx * d;
    const p0 = torsoPt(th(0.95), 0.905, hoodOff + 0.004), p1 = torsoPt(th(1.22), 1.03, hoodOff + 0.004);
    const pts = [p0, p0.clone().lerp(p1, 0.5).add(V3(0, 0, 0.002)), p1];
    const lip = add(new THREE.TubeGeometry(new THREE.CatmullRomCurve3(pts), 16, 0.0022, 6), M.hoodieDark); hoodieParts.push(lip);
  }
  // drawstrings with eyelets
  for (const sx of [-1, 1]) {
    const topIdx = (hrs.length - 7) * NVH + (sx > 0 ? 96 : 0);
    const x0 = pa.getX(topIdx) - sx * 0.012;
    const pts = []; let zPrev = -1;
    for (let y = 1.475; y >= 1.22; y -= 0.015) {
      const p = torso(y); const q = Math.min(Math.abs(x0) / p.a, 0.99); const n = nAt(y, NF);
      const z = Math.max(p.df * Math.pow(1 - Math.pow(q, n), 1 / n) + 0.007, zPrev);
      zPrev = z; pts.push(V3(x0 + sx * 0.002 * Math.sin(y * 40), y, z));
    }
    hoodieParts.push(add(new THREE.TubeGeometry(new THREE.CatmullRomCurve3(pts), 40, 0.0033, 6), M.cord));
    const tip = add(new THREE.CylinderGeometry(0.0041, 0.0041, 0.024, 12), M.zip);
    tip.position.copy(pts[pts.length - 1]).add(V3(0, -0.012, 0)); hoodieParts.push(tip);
    const eye = add(new THREE.TorusGeometry(0.0045, 0.0014, 6, 16), M.zip); eye.position.copy(pts[0]).add(V3(0, 0.004, 0.002)); hoodieParts.push(eye);
  }
  // hood: rolled collar around the neck + draped hood on the upper back with a hemmed edge
  const hoodRing = add(new THREE.TorusGeometry(rN + 0.038, 0.032, 16, 48, Math.PI * 1.45), M.hoodie);
  hoodRing.rotation.set(Math.PI / 2, 0, Math.PI / 2 + Math.PI * 0.275);
  hoodRing.position.set(0, 1.5, -0.008); hoodRing.scale.set(1.1, 1.1, 0.85);
  const tb = torso(1.45);
  const bagRings = [];
  for (let i = 0; i <= 14; i++) {
    const t = i / 14;
    const y = lerp(1.37, 1.515, t);
    const w = (0.07 + 0.075 * Math.sin(Math.PI * Math.min(1, t * 1.15))) * (1 + 0.12 * fc);
    bagRings.push({ c: V3(0, y, -(torso(y).db + 0.022 + 0.018 * Math.sin(Math.PI * t))), R: AXIS_X, F: AXIS_Z, a: w, df: 0.012 + 0.02 * t, db: 0.03 * Math.sin(Math.PI * t) + 0.006, nf: 2, nb: 2.3, t0: Math.PI + 0.05, t1: TAU - 0.05,
      off: (th) => 0.003 * Math.sin(th * 6 + t * 5) * Math.sin(Math.PI * t) });
  }
  const bag = add(loft(bagRings, 40, false), M.hoodie);
  const edgePts = []; for (let i = 0; i <= 14; i++) { const r = bagRings[i]; edgePts.push(V3(...sePoint(r, TAU - 0.05)).add(V3(0, 0, 0.001))); }
  for (let i = 14; i >= 0; i--) { const r = bagRings[i]; edgePts.push(V3(...sePoint(r, Math.PI + 0.05))); }
  const hem = add(new THREE.TubeGeometry(new THREE.CatmullRomCurve3(edgePts), 60, 0.004, 6), M.hoodieDark);
  hoodieParts.push(hoodRing, bag, hem);

  fig.userData.hoodieParts = hoodieParts;
  fig.userData.glassesParts = glassesParts;
  fig.userData.teeParts = teeParts;
  return fig;
}

/* Scene controls follow. */
renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
renderer.outputColorSpace = THREE.SRGBColorSpace;
renderer.toneMapping = THREE.ACESFilmicToneMapping;
renderer.toneMappingExposure = 1.05;
renderer.shadowMap.enabled = true;
renderer.shadowMap.type = THREE.PCFSoftShadowMap;

const scene = new THREE.Scene();
const pmrem = new THREE.PMREMGenerator(renderer);
const room = new RoomEnvironment(renderer);
const environment = pmrem.fromScene(room, 0.04);
room.dispose();
scene.environment = environment.texture;
const camera = new THREE.PerspectiveCamera(22, 1, 0.1, 50);
scene.add(new THREE.HemisphereLight(0xf4f6f2, 0x5c625b, 0.95));
const key = new THREE.DirectionalLight(0xfff4e8, 2.2);
key.position.set(-2.2, 4.2, 3.4);
key.castShadow = true;
key.shadow.mapSize.set(2048, 2048);
Object.assign(key.shadow.camera, { left: -3.6, right: 3.6, top: 2.6, bottom: -1.2, near: 0.5, far: 14 });
key.shadow.bias = -0.0004; key.shadow.normalBias = 0.015;
scene.add(key);
const rim = new THREE.DirectionalLight(0xdfe9ff, 1.2); rim.position.set(2.5, 2.8, -3.2); scene.add(rim);
const fill = new THREE.DirectionalLight(0xffffff, 0.45); fill.position.set(3, 1.2, 2.5); scene.add(fill);
const groundMat = new THREE.ShadowMaterial({ opacity: 0.22 });
const ground = new THREE.Mesh(new THREE.PlaneGeometry(14, 8), groundMat);
ground.rotation.x = -Math.PI / 2; ground.receiveShadow = true; scene.add(ground);

let weights: BodySceneWeights = [...options.weights];
let heightScale = Number.isFinite(options.heightCm) && options.heightCm > 0 ? options.heightCm / 176 : 1;
let spinning = false;
let hoodieOn = true;
let glassesOn = false;
let closeUp = false;
let angle = 0;
let velocity = 0;
let target: number | null = null;
let dragging = false;
let lastX = 0;
let lastT = 0;
let active = true;
let inViewport = true;
let disposed = false;
let contextLost = false;
let raf = 0;
let lastFrame = 0;
const holders = [0, 1, 2].map(() => { const group = new THREE.Group(); scene.add(group); return group; });
const figures: Array<THREE.Group | null> = [null, null, null];

function disposeGroup(group: THREE.Group) {
  const seen = new Set<THREE.BufferGeometry>();
  group.traverse(object => {
    if (object instanceof THREE.Mesh && !object.geometry.userData.shared && !seen.has(object.geometry)) {
      seen.add(object.geometry);
      object.geometry.dispose();
    }
  });
}
function setFigure(index: number) {
  const previous = figures[index];
  if (previous) { holders[index].remove(previous); disposeGroup(previous); }
  const weight = weights[index];
  if (weight === null || !Number.isFinite(weight) || weight <= 0) { figures[index] = null; return; }
  const figure = buildFigure(weight);
  figure.userData.hoodieParts.forEach((part: THREE.Object3D) => { part.visible = hoodieOn; });
  figure.userData.teeParts.forEach((part: THREE.Object3D) => { part.visible = !hoodieOn; });
  figure.userData.glassesParts.forEach((part: THREE.Object3D) => { part.visible = glassesOn; });
  figures[index] = figure;
  holders[index].add(figure);
}
weights.forEach((_, index) => setFigure(index));
holders.forEach(holder => holder.scale.setScalar(heightScale));

const cam = { y: 1.12, look: 0.9, d: 6, dx: 1 };
const camT = { ...cam };
let renderWidth = 0;
let renderHeight = 0;
function layout(snap = false) {
  const width = host.clientWidth;
  const height = host.clientHeight;
  if (!width || !height || disposed) return;
  if (width !== renderWidth || height !== renderHeight) {
    renderer.setSize(width, height, false);
    renderWidth = width;
    renderHeight = height;
  }
  const aspect = width / height;
  camera.aspect = aspect;
  const tanH = Math.tan(THREE.MathUtils.degToRad(camera.fov / 2));
  const needHalfH = (closeUp ? 0.34 : 1.0) * heightScale;
  const needHalfW = (closeUp ? 1.12 : 1.28) * heightScale;
  const distance = Math.max(needHalfH / tanH, needHalfW / (tanH * aspect));
  const halfW = distance * tanH * aspect;
  const halfHA = distance * tanH;
  const look = closeUp ? Math.max(0.9, HEIGHT * heightScale + 0.07 - halfHA) : 0.9 * heightScale;
  Object.assign(camT, { d: distance, y: look + (closeUp ? 0.08 : 0.22) * heightScale, look, dx: halfW * 2 / 3 });
  if (snap) Object.assign(cam, camT);
  camera.updateProjectionMatrix();
}
function applyCam() {
  camera.position.set(0, cam.y, cam.d);
  camera.lookAt(0, cam.look, 0);
  holders[0].position.x = -cam.dx;
  holders[1].position.x = 0;
  holders[2].position.x = cam.dx;
  holders.forEach(holder => { holder.rotation.y = angle; });
}
function renderScene() {
  if (disposed || contextLost || !active) return;
  applyCam();
  renderer.render(scene, camera);
}
function needsFrame() {
  return spinning || target !== null || Math.abs(velocity) > 0.0004 ||
    (['y', 'look', 'd', 'dx'] as const).some(key => Math.abs(cam[key] - camT[key]) > 0.0005);
}
function tick(now: number) {
  raf = 0;
  if (!active || disposed || contextLost) return;
  const dt = lastFrame ? Math.min(0.05, (now - lastFrame) / 1000) : 0;
  lastFrame = now;
  if (!dragging) {
    if (target !== null) {
      angle += (target - angle) * Math.min(1, dt * 7);
      if (Math.abs(target - angle) < 0.0005) { angle = target; target = null; }
    } else if (Math.abs(velocity) > 0.0004) {
      angle += velocity;
      velocity *= Math.pow(0.05, dt);
      if (Math.abs(velocity) <= 0.0004) velocity = 0;
    } else if (spinning) {
      angle += dt * 0.4;
    }
  }
  const k = Math.min(1, dt * 5);
  for (const key of ['y', 'look', 'd', 'dx'] as const) cam[key] += (camT[key] - cam[key]) * k;
  renderScene();
  if (needsFrame()) raf = requestAnimationFrame(tick);
}
function scheduleFrame() {
  if (disposed || contextLost || !active) return;
  if (!raf) raf = requestAnimationFrame(tick);
}
requestRender = () => {
  if (disposed) { headTex?.dispose(); earTex?.dispose(); return; }
  scheduleFrame();
};
function setTheme(theme: ThemeName) {
  groundMat.opacity = theme === 'dark' ? 0.32 : theme === 'ocean' ? 0.20 : theme === 'sunset' ? 0.24 : 0.22;
  groundMat.needsUpdate = true;
  scheduleFrame();
}
function setWeights(next: BodySceneWeights, heightCm?: number) {
  if (disposed) return;
  let changed = false;
  if (heightCm !== undefined && Number.isFinite(heightCm) && heightCm > 0 && heightCm / 176 !== heightScale) {
    heightScale = heightCm / 176;
    holders.forEach(holder => holder.scale.setScalar(heightScale));
    layout();
    changed = true;
  }
  next.forEach((weight, index) => {
    if (weights[index] !== weight) {
      weights[index] = weight;
      setFigure(index);
      changed = true;
    }
  });
  if (changed) scheduleFrame();
}
function setSpinning(enabled: boolean) {
  if (disposed || spinning === enabled) return;
  spinning = enabled;
  if (enabled) { target = null; velocity = 0; scheduleFrame(); return; }
  if (target !== null) { scheduleFrame(); return; }
  velocity = 0;
  if (raf) { cancelAnimationFrame(raf); raf = 0; }
  lastFrame = 0;
  renderScene();
}
function setView(view: BodySceneView) {
  if (disposed) return;
  spinning = false;
  velocity = 0;
  const viewAngle = view === 'side' ? Math.PI / 2 : view === 'back' ? Math.PI : 0;
  if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) {
    angle = viewAngle;
    target = null;
    if (raf) { cancelAnimationFrame(raf); raf = 0; }
    lastFrame = 0;
    renderScene();
    return;
  }
  target = angle + THREE.MathUtils.euclideanModulo(viewAngle - angle + Math.PI, TAU) - Math.PI;
  scheduleFrame();
}
function setCloseUp(enabled: boolean) {
  if (disposed || closeUp === enabled) return;
  closeUp = enabled;
  // Paused scenes should redraw once; interpolating each software WebGL frame
  // makes the control sluggish and adds no value for reduced-motion users.
  layout(!spinning && target === null);
  if (!spinning && target === null) {
    if (raf) { cancelAnimationFrame(raf); raf = 0; }
    lastFrame = 0;
    renderScene();
  } else {
    scheduleFrame();
  }
}
function setHoodie(enabled: boolean) {
  if (disposed || hoodieOn === enabled) return;
  hoodieOn = enabled;
  figures.forEach(figure => {
    figure?.userData.hoodieParts.forEach((part: THREE.Object3D) => { part.visible = enabled; });
    figure?.userData.teeParts.forEach((part: THREE.Object3D) => { part.visible = !enabled; });
  });
  scheduleFrame();
}
function setGlasses(enabled: boolean) {
  if (disposed || glassesOn === enabled) return;
  glassesOn = enabled;
  figures.forEach(figure => figure?.userData.glassesParts.forEach((part: THREE.Object3D) => { part.visible = enabled; }));
  scheduleFrame();
}
function pointerDown(event: PointerEvent) {
  dragging = true;
  lastX = event.clientX;
  lastT = performance.now();
  velocity = 0;
  target = null;
  try { canvas.setPointerCapture(event.pointerId); } catch { /* Capture can fail during teardown. */ }
}
function pointerMove(event: PointerEvent) {
  if (!dragging) return;
  const now = performance.now();
  const dx = event.clientX - lastX;
  angle += dx * 0.012;
  velocity = dx * 0.012 / Math.max(16, now - lastT) * 16;
  lastX = event.clientX;
  lastT = now;
  scheduleFrame();
}
function pointerEnd() { dragging = false; scheduleFrame(); }
function updateVisibility() {
  const next = inViewport && document.visibilityState === 'visible';
  if (active === next) return;
  active = next;
  if (!active) { if (raf) cancelAnimationFrame(raf); raf = 0; lastFrame = 0; }
  else scheduleFrame();
}
function contextLostHandler(event: Event) {
  event.preventDefault();
  contextLost = true;
  if (raf) cancelAnimationFrame(raf);
  raf = 0;
  options.onUnavailable?.();
}
const resizeObserver = new ResizeObserver(() => { layout(true); scheduleFrame(); });
resizeObserver.observe(host);
const intersectionObserver = typeof IntersectionObserver === 'undefined' ? null : new IntersectionObserver(([entry]) => {
  inViewport = entry.isIntersecting;
  updateVisibility();
});
intersectionObserver?.observe(host);
document.addEventListener('visibilitychange', updateVisibility);
canvas.addEventListener('pointerdown', pointerDown);
canvas.addEventListener('pointermove', pointerMove);
canvas.addEventListener('pointerup', pointerEnd);
canvas.addEventListener('pointercancel', pointerEnd);
canvas.addEventListener('webglcontextlost', contextLostHandler);
layout(true);
setTheme(options.theme);
scheduleFrame();

function dispose() {
  if (disposed) return;
  disposed = true;
  if (raf) cancelAnimationFrame(raf);
  resizeObserver.disconnect();
  intersectionObserver?.disconnect();
  document.removeEventListener('visibilitychange', updateVisibility);
  canvas.removeEventListener('pointerdown', pointerDown);
  canvas.removeEventListener('pointermove', pointerMove);
  canvas.removeEventListener('pointerup', pointerEnd);
  canvas.removeEventListener('pointercancel', pointerEnd);
  canvas.removeEventListener('webglcontextlost', contextLostHandler);
  figures.forEach(figure => { if (figure) disposeGroup(figure); });
  const shared = new Set<THREE.BufferGeometry>();
  GLASSES.traverse(object => { if (object instanceof THREE.Mesh) shared.add(object.geometry); });
  if (EAR_GEO) shared.add(EAR_GEO);
  shared.forEach(geometry => geometry.dispose());
  Object.values(M).forEach(material => material.dispose());
  [fishTex, denimTex, denimNormal, knitNormal, teethTex, lensTex, watchFaceTex, headTex, earTex].forEach(texture => texture?.dispose());
  ground.geometry.dispose();
  groundMat.dispose();
  key.shadow.dispose();
  environment.dispose();
  pmrem.dispose();
  renderer.dispose();
  renderer.forceContextLoss();
  canvas.remove();
}
return { setWeights, setTheme, setSpinning, setView, setCloseUp, setHoodie, setGlasses, render: renderScene, dispose };
}
