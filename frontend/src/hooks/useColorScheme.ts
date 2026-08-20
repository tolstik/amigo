import { useTheme } from "../theme/ThemeProvider";

export type ColorScheme = "light" | "dark";

export function useColorScheme(): ColorScheme {
  const { theme } = useTheme();
  return theme === "dark" ? "dark" : "light";
}
