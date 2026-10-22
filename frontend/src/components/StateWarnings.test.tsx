import { act, cleanup, render, renderHook, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { Checked } from "../stateRules";
import { checkQuery, HOW, useStateCheck } from "../stateRules";
import { StateWarnings } from "./StateWarnings";

afterEach(() => {
  cleanup();
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

const NOTE = "No attorney has reviewed these state rules. They come from research.";

function found(warnings: Checked["warnings"], state: string | null = "CA"): Checked {
  return { state, rules: true, warnings, note: NOTE };
}

const WAIT = {
  rule: "late fee wait",
  said: "California: no late fee until rent is unpaid five days after its due date (Test Code 1-1).",
  cite: "Test Code 1-1",
  how: "excerpt" as const,
};

describe("StateWarnings", () => {
  it("shows each warning, that it refuses nothing, the note and a link to the table", () => {
    render(<StateWarnings checked={found([WAIT, { ...WAIT, rule: "late fee cap", said: "California: at most $50.00.", how: "secondary" }])} label="State rules for these late fees" />);

    const box = screen.getByRole("region", { name: "State rules for these late fees" });
    expect(within(box).getByText("CA rules: a warning, not a refusal")).toBeTruthy();
    expect(within(box).getAllByRole("listitem").map((li) => li.textContent)).toEqual([
      WAIT.said,
      "California: at most $50.00. (Not verified: a summary only.)",
    ]);
    expect(box.textContent).toContain(NOTE);
    expect(within(box).getByRole("link", { name: "The state rules" }).getAttribute("href")).toBe("/settings/state-rules");
  });

  it("says why nothing was checked without an address, and shows nothing when nothing was found", () => {
    const { container, rerender } = render(<StateWarnings checked={found([], null)} label="x" />);
    expect(container.textContent).toBe("No address on file for this property, so no state's rules were checked.");

    rerender(<StateWarnings checked={found([])} label="x" />);
    expect(container.textContent).toBe("");
    rerender(<StateWarnings checked={null} label="x" />);
    expect(container.textContent).toBe("");
  });
});

describe("checkQuery", () => {
  it("leaves out what is empty and trims the rest", () => {
    expect(checkQuery({ property_id: "p1", grace_days: " 5 ", flat_fee: "", maximum: null, percent: undefined })).toBe(
      "property_id=p1&grace_days=5",
    );
  });

  it("names how each rule was checked", () => {
    expect(HOW.secondary).toBe("a summary only");
  });
});

describe("useStateCheck", () => {
  it("asks a moment after the form stops changing, keeping only the last answer", async () => {
    vi.useFakeTimers();
    const fetch = vi.fn(async (path: string) =>
      new Response(JSON.stringify(found(path.includes("grace_days=3") ? [WAIT] : [])), { status: 200 }));
    vi.stubGlobal("fetch", fetch);

    const { result, rerender } = renderHook(({ path }: { path: string | null }) => useStateCheck(path), {
      initialProps: { path: "/api/state-rules/late-fees?grace_days=" as string | null },
    });
    rerender({ path: "/api/state-rules/late-fees?grace_days=3" });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(300);
    });

    expect(fetch).toHaveBeenCalledTimes(1);
    expect(result.current?.warnings).toEqual([WAIT]);

    rerender({ path: null });
    expect(result.current).toBeNull();
  });

  it("shows nothing for an answer that isn't a check's", async () => {
    vi.useFakeTimers();
    vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({ name: "Elm Court" }), { status: 200 })));

    const { result } = renderHook(() => useStateCheck("/api/state-rules/late-fees?grace_days=1"));
    await act(async () => {
      await vi.advanceTimersByTimeAsync(300);
    });

    expect(result.current).toBeNull();
  });

  it("shows nothing when the check is refused (a number half typed)", async () => {
    vi.useFakeTimers();
    vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({ detail: "bad" }), { status: 422 })));

    const { result } = renderHook(() => useStateCheck("/api/state-rules/late-fees?percent=1."));
    await act(async () => {
      await vi.advanceTimersByTimeAsync(300);
    });

    expect(result.current).toBeNull();
  });
});
