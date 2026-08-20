import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { THEME_STORAGE_KEY, ThemeProvider } from "../theme/ThemeProvider";
import { ThemeSwitcher } from "./ThemeSwitcher";

function renderSwitcher() {
  return render(<ThemeProvider><ThemeSwitcher /></ThemeProvider>);
}

describe("ThemeSwitcher", () => {
  beforeEach(() => {
    window.localStorage.clear();
    document.documentElement.dataset.theme = "light";
    document.documentElement.style.colorScheme = "light";
    document.querySelector('meta[name="theme-color"]')?.remove();
    const meta = document.createElement("meta");
    meta.name = "theme-color";
    meta.content = "#f4f6f1";
    document.head.append(meta);
  });

  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
  });

  it("uses light for a fresh visit even when the operating system prefers dark", () => {
    const matchMedia = vi.fn(() => ({ matches: true }));
    vi.stubGlobal("matchMedia", matchMedia);

    renderSwitcher();

    expect(screen.getByRole("combobox", { name: "Тема оформления" })).toHaveValue("light");
    expect(document.documentElement).toHaveAttribute("data-theme", "light");
    expect(window.localStorage.getItem(THEME_STORAGE_KEY)).toBeNull();
    expect(matchMedia).not.toHaveBeenCalled();
  });

  it("offers four named themes and persists an explicit choice", async () => {
    const user = userEvent.setup();
    renderSwitcher();
    const selector = screen.getByRole("combobox", { name: "Тема оформления" });

    expect(screen.getAllByRole("option").map((option) => option.textContent)).toEqual([
      "Светлая",
      "Тёмная",
      "Океан",
      "Закат",
    ]);

    await user.selectOptions(selector, "dark");
    expect(document.documentElement).toHaveAttribute("data-theme", "dark");
    expect(document.documentElement.style.colorScheme).toBe("dark");
    expect(document.querySelector('meta[name="theme-color"]')).toHaveAttribute("content", "#101713");
    expect(window.localStorage.getItem(THEME_STORAGE_KEY)).toBe("dark");
  });

  it("restores a previously selected theme", () => {
    window.localStorage.setItem(THEME_STORAGE_KEY, "ocean");
    renderSwitcher();

    expect(screen.getByRole("combobox", { name: "Тема оформления" })).toHaveValue("ocean");
    expect(document.documentElement).toHaveAttribute("data-theme", "ocean");
    expect(document.documentElement.style.colorScheme).toBe("light");
  });

  it("falls back to light when storage contains an unknown theme", () => {
    window.localStorage.setItem(THEME_STORAGE_KEY, "system");
    renderSwitcher();

    expect(screen.getByRole("combobox", { name: "Тема оформления" })).toHaveValue("light");
    expect(document.documentElement).toHaveAttribute("data-theme", "light");
  });
});
