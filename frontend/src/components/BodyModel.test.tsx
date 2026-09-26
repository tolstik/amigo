import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { ThemeProvider } from "../theme/ThemeProvider";
import BodyModel from "./BodyModel";

const sceneSpy = vi.hoisted(() => ({ create: vi.fn(), setWeights: vi.fn() }));
vi.mock("./bodyScene", () => ({
  createBodyScene: vi.fn((...args: unknown[]) => {
    sceneSpy.create(...args);
    return {
    setWeights: sceneSpy.setWeights,
    setTheme: vi.fn(),
    setSpinning: vi.fn(),
    setView: vi.fn(),
    setCloseUp: vi.fn(),
    setHoodie: vi.fn(),
    setGlasses: vi.fn(),
    render: vi.fn(),
    dispose: vi.fn(),
    };
  }),
}));

afterEach(() => {
  cleanup();
  sceneSpy.create.mockClear();
  sceneSpy.setWeights.mockClear();
  vi.unstubAllGlobals();
});

it("updates the current figure when a new Withings weight arrives without changing the program endpoints", async () => {
  vi.stubGlobal("IntersectionObserver", undefined);
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ ok: false }));
  const props = { startKg: 127.03, latestKg: 125.5, latestAt: "2026-09-01T05:00:00Z", targetKg: 76.5, heightCm: 176 };
  const view = render(<ThemeProvider><BodyModel {...props} /></ThemeProvider>);
  await waitFor(() => expect(sceneSpy.create).toHaveBeenCalled());
  expect(sceneSpy.create.mock.calls[0][1].weights).toEqual([127.03, 125.5, 76.5]);
  expect(screen.getByText("127,03 кг")).toBeInTheDocument();
  expect(screen.getByText("125,5 кг")).toBeInTheDocument();
  expect(screen.getByText("76,5 кг")).toBeInTheDocument();

  view.rerender(<ThemeProvider><BodyModel {...props} latestKg={124.75} latestAt="2026-09-26T05:00:00Z" /></ThemeProvider>);
  await waitFor(() => expect(sceneSpy.setWeights).toHaveBeenLastCalledWith([127.03, 124.75, 76.5], 176));
  expect(screen.getByText("124,75 кг")).toBeInTheDocument();
  expect(screen.getByText(/Замер 26 сент. 2026/)).toBeInTheDocument();

  view.rerender(<ThemeProvider><BodyModel {...props} latestKg={null} latestAt={null} /></ThemeProvider>);
  await waitFor(() => expect(sceneSpy.setWeights).toHaveBeenLastCalledWith([127.03, null, 76.5], 176));
  expect(screen.getByText("Нет замера", { selector: ".body-model__label-weight" })).toBeInTheDocument();
});
