import { createContext, useCallback, useContext, useLayoutEffect, useMemo, useState, type ReactNode } from "react";

export const THEME_STORAGE_KEY = "amigo-theme";

export const themes = [
  { id: "light", label: "Светлая", colorScheme: "light", browserColor: "#f4f6f1" },
  { id: "dark", label: "Тёмная", colorScheme: "dark", browserColor: "#101713" },
  { id: "ocean", label: "Океан", colorScheme: "light", browserColor: "#eaf4f7" },
  { id: "sunset", label: "Закат", colorScheme: "light", browserColor: "#fbf1e7" },
] as const;

export type ThemeName = (typeof themes)[number]["id"];

interface ThemeContextValue {
  theme: ThemeName;
  setTheme: (theme: ThemeName) => void;
}

const defaultTheme: ThemeName = "light";
const ThemeContext = createContext<ThemeContextValue | null>(null);

export function isThemeName(value: string | null): value is ThemeName {
  return themes.some((theme) => theme.id === value);
}

export function storedTheme(): ThemeName {
  if (typeof window === "undefined") return defaultTheme;
  try {
    const value = window.localStorage.getItem(THEME_STORAGE_KEY);
    return isThemeName(value) ? value : defaultTheme;
  } catch {
    return defaultTheme;
  }
}

export function applyTheme(theme: ThemeName) {
  if (typeof document === "undefined") return;
  const config = themes.find((item) => item.id === theme) ?? themes[0];
  document.documentElement.dataset.theme = config.id;
  document.documentElement.style.colorScheme = config.colorScheme;
  document.querySelector<HTMLMetaElement>('meta[name="theme-color"]')?.setAttribute("content", config.browserColor);
}

export function applyStoredTheme(): ThemeName {
  const theme = storedTheme();
  applyTheme(theme);
  return theme;
}

export function ThemeProvider({ children }: { children: ReactNode }) {
  const [theme, setThemeState] = useState<ThemeName>(storedTheme);

  useLayoutEffect(() => {
    applyTheme(theme);
  }, [theme]);

  const setTheme = useCallback((nextTheme: ThemeName) => {
    if (!isThemeName(nextTheme)) return;
    applyTheme(nextTheme);
    try {
      window.localStorage.setItem(THEME_STORAGE_KEY, nextTheme);
    } catch {
      // The selected theme still works for this page when storage is unavailable.
    }
    setThemeState(nextTheme);
  }, []);

  const value = useMemo(() => ({ theme, setTheme }), [setTheme, theme]);
  return <ThemeContext.Provider value={value}>{children}</ThemeContext.Provider>;
}

export function useTheme(): ThemeContextValue {
  const value = useContext(ThemeContext);
  if (!value) throw new Error("useTheme must be used inside ThemeProvider");
  return value;
}
