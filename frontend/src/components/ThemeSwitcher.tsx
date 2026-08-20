import { themes, useTheme, type ThemeName } from "../theme/ThemeProvider";

export function ThemeSwitcher() {
  const { theme, setTheme } = useTheme();

  return (
    <label className="theme-picker">
      <span className={`theme-picker__swatch theme-picker__swatch--${theme}`} aria-hidden="true" />
      <span className="theme-picker__label">Тема</span>
      <select
        aria-label="Тема оформления"
        value={theme}
        onChange={(event) => setTheme(event.currentTarget.value as ThemeName)}
      >
        {themes.map((item) => <option key={item.id} value={item.id}>{item.label}</option>)}
      </select>
    </label>
  );
}
