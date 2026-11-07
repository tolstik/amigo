interface SparklineProps {
  values: Array<number | null>;
  label: string;
  /** Lower values are better (for example body weight). Only affects the end marker. */
  className?: string;
}

const WIDTH = 120;
const HEIGHT = 34;
const PAD = 3;

/** Build SVG path segments; missing values break the line instead of interpolating. */
export function sparklinePath(values: Array<number | null>): string {
  const numeric = values.filter((value): value is number => value !== null && Number.isFinite(value));
  if (numeric.length < 2) return "";
  const min = Math.min(...numeric);
  const max = Math.max(...numeric);
  const span = max - min || 1;
  const step = values.length > 1 ? (WIDTH - PAD * 2) / (values.length - 1) : 0;
  let path = "";
  let drawing = false;
  values.forEach((value, index) => {
    if (value === null || !Number.isFinite(value)) {
      drawing = false;
      return;
    }
    const x = PAD + index * step;
    const y = PAD + (HEIGHT - PAD * 2) * (1 - (value - min) / span);
    path += `${drawing ? "L" : "M"}${x.toFixed(1)} ${y.toFixed(1)}`;
    drawing = true;
  });
  return path;
}

export function Sparkline({ values, label, className }: SparklineProps) {
  const path = sparklinePath(values);
  if (!path) return null;
  const lastIndex = values.reduce<number>((found, value, index) => (value !== null && Number.isFinite(value) ? index : found), -1);
  const numeric = values.filter((value): value is number => value !== null && Number.isFinite(value));
  const min = Math.min(...numeric);
  const span = Math.max(...numeric) - min || 1;
  const step = values.length > 1 ? (WIDTH - PAD * 2) / (values.length - 1) : 0;
  const last = values[lastIndex] as number;
  return (
    <svg className={`sparkline${className ? ` ${className}` : ""}`} viewBox={`0 0 ${WIDTH} ${HEIGHT}`} preserveAspectRatio="none" role="img" aria-label={label}>
      <path d={path} fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" vectorEffect="non-scaling-stroke" />
      <circle cx={PAD + lastIndex * step} cy={PAD + (HEIGHT - PAD * 2) * (1 - (last - min) / span)} r="2.6" fill="currentColor" />
    </svg>
  );
}
