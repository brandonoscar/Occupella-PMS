import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiError, api } from "../api";
import { notify } from "../toast";
import { Said } from "./Said";
import { Toaster } from "./Toaster";

// What the toasts show, drawn from their attributes.
const shown = () => [...document.querySelectorAll("[data-testid=toaster] [data-text]")].map((t) => t.getAttribute("data-text"));

afterEach(() => {
  cleanup();
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

describe("toasts", () => {
  it("shows a save's message for a few seconds, and a refusal a little longer", () => {
    vi.useFakeTimers();
    render(<Toaster />);
    act(() => {
      notify("success", "recorded the payment of $1,395.00");
      notify("error", "not enough money");
    });
    const toaster = screen.getByTestId("toaster");
    expect(shown()).toEqual(["recorded the payment of $1,395.00", "not enough money"]);
    // Not announced a second time, nor its words in the page twice: the page's own line says it.
    expect(toaster.getAttribute("aria-hidden")).toBe("true");
    expect(toaster.textContent).toBe("");
    act(() => vi.advanceTimersByTime(5000));
    expect(shown()).toEqual(["not enough money"]);
    act(() => vi.advanceTimersByTime(3000));
    expect(screen.queryByTestId("toaster")).toBeNull();
  });

  it("closes one when asked, and shows no more than four", () => {
    render(<Toaster />);
    act(() => {
      for (const n of [1, 2, 3, 4, 5]) notify("success", `saved ${n}`);
    });
    expect(shown()).toEqual(["saved 2", "saved 3", "saved 4", "saved 5"]);
    fireEvent.click(screen.getAllByRole("button", { hidden: true })[0]!);
    expect(shown()).toEqual(["saved 3", "saved 4", "saved 5"]);
    act(() => notify("success", ""));
    expect(screen.getByTestId("toaster").children).toHaveLength(3);
  });

  it("raises a toast from what a page said, keeping a status line for a screen reader", () => {
    render(<><Said>Renamed Property 1A.</Said><Toaster /></>);
    expect(screen.getByRole("status").textContent).toBe("Renamed Property 1A.");
    expect(screen.getByRole("status").className).toContain("sr-only");
    expect(shown()).toContain("Renamed Property 1A.");
  });

  it("says nothing when nothing was said", () => {
    const { container } = render(<Said>{""}</Said>);
    expect(container.textContent).toBe("");
  });

  it("raises a refused change's reason, wherever the page shows it", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({ detail: "not enough money" }), { status: 409 })));
    render(<Toaster />);
    await act(async () => {
      await expect(api.post("/api/payables/draws", {})).rejects.toBeInstanceOf(ApiError);
    });
    expect(shown()).toContain("not enough money");
  });
});
