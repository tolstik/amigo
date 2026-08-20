import type { EChartsOption } from "echarts";
import type { ThemeName } from "../theme/ThemeProvider";

interface ChartPalette {
  green: string;
  greenSoft: string;
  blue: string;
  violet: string;
  coral: string;
  amber: string;
  muted: string;
  grid: string;
  forecastArea: string;
}

export const chartPalettes: Record<ThemeName, ChartPalette> = {
  light: {
    green: "#2d9365",
    greenSoft: "#77c69d",
    blue: "#4b7bec",
    violet: "#8b6ee8",
    coral: "#e9785d",
    amber: "#d99b35",
    muted: "#6d796f",
    grid: "rgba(128, 145, 134, .16)",
    forecastArea: "rgba(139,110,232,.06)",
  },
  dark: {
    green: "#62c394",
    greenSoft: "#8bd9b2",
    blue: "#80a3ff",
    violet: "#ad99ff",
    coral: "#f29a84",
    amber: "#e0b15f",
    muted: "#a7b5ab",
    grid: "rgba(207, 230, 214, .14)",
    forecastArea: "rgba(173,153,255,.12)",
  },
  ocean: {
    green: "#078d84",
    greenSoft: "#55b9ae",
    blue: "#3976d2",
    violet: "#745ec5",
    coral: "#cf6664",
    amber: "#ad7415",
    muted: "#5f7880",
    grid: "rgba(30, 83, 99, .16)",
    forecastArea: "rgba(116,94,197,.09)",
  },
  sunset: {
    green: "#71864d",
    greenSoft: "#9dac75",
    blue: "#5877a8",
    violet: "#865d84",
    coral: "#c65345",
    amber: "#9b6817",
    muted: "#846c62",
    grid: "rgba(104, 61, 43, .16)",
    forecastArea: "rgba(134,93,132,.09)",
  },
};

function themeReplacements(theme: ThemeName): Map<string, string> {
  const source = chartPalettes.light;
  const target = chartPalettes[theme];
  return new Map(Object.keys(source).map((key) => [
    source[key as keyof ChartPalette],
    target[key as keyof ChartPalette],
  ]));
}

function replaceThemeValues(value: unknown, replacements: Map<string, string>, seen: WeakMap<object, unknown>): unknown {
  if (typeof value === "string") return replacements.get(value) ?? value;
  if (typeof value === "function") {
    return function (this: unknown, ...args: unknown[]) {
      return replaceThemeValues(value.apply(this, args), replacements, seen);
    };
  }
  if (!value || typeof value !== "object") return value;
  const existing = seen.get(value);
  if (existing) return existing;
  if (Array.isArray(value)) {
    const result: unknown[] = [];
    seen.set(value, result);
    value.forEach((item) => result.push(replaceThemeValues(item, replacements, seen)));
    return result;
  }
  if (Object.getPrototypeOf(value) !== Object.prototype && Object.getPrototypeOf(value) !== null) return value;
  const result: Record<string, unknown> = {};
  seen.set(value, result);
  Object.entries(value).forEach(([key, item]) => {
    result[key] = replaceThemeValues(item, replacements, seen);
  });
  return result;
}

export function chartOptionForTheme(option: EChartsOption, theme: ThemeName): EChartsOption {
  if (theme === "light") return option;
  return replaceThemeValues(option, themeReplacements(theme), new WeakMap()) as EChartsOption;
}
