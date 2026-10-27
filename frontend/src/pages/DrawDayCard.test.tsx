import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { DrawDay } from "../payouts";
import { DrawDayCard, drawDayLine } from "./DrawDayCard";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

const NONE: DrawDay = { draw_day: null, changed_at: null, changed_by: null };

function server(day: DrawDay, refuse?: string) {
  let now = day;
  const fetch = vi.fn(async (_path: string, init?: RequestInit) => {
    if (init?.method === "POST") {
      if (refuse) return new Response(JSON.stringify({ detail: refuse }), { status: 403 });
      now = { ...now, ...JSON.parse(String(init.body)), changed_by: "Staff 1" };
    }
    return new Response(JSON.stringify(now), { status: 200 });
  });
  vi.stubGlobal("fetch", fetch);
  return fetch;
}

describe("DrawDayCard", () => {
  it("says when owners' draws are prepared", () => {
    expect(drawDayLine(NONE)).toBe("No draw day: staff pay owners on Pay owners when they choose.");
    expect(drawDayLine({ draw_day: 22 })).toBe(
      "The night of the 22nd of each month, every owner's draw is prepared for staff to pay.",
    );
  });

  it("sets a draw day, and takes it away again", async () => {
    const fetch = server(NONE);
    render(<DrawDayCard />);
    expect((await screen.findByTestId("draw-day")).textContent).toContain("No draw day");
    fireEvent.click(screen.getByRole("button", { name: "Edit draw day" }));
    let panel = screen.getByRole("dialog", { name: "Owner draws" });
    fireEvent.change(within(panel).getByLabelText("Draw day"), { target: { value: "15" } });
    fireEvent.click(within(panel).getByRole("button", { name: "Save" }));

    expect(await screen.findByText(/The night of the 15th of each month/)).toBeTruthy();
    expect(screen.queryByRole("dialog")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Edit draw day" }));
    panel = screen.getByRole("dialog", { name: "Owner draws" });
    fireEvent.change(within(panel).getByLabelText("Draw day"), { target: { value: "" } });
    fireEvent.click(within(panel).getByRole("button", { name: "Save" }));
    expect(await screen.findByText(/No draw day/)).toBeTruthy();

    const posts = fetch.mock.calls.filter(([, init]) => init?.method === "POST");
    expect(posts.map(([path]) => path)).toEqual(["/api/payouts/draw-day", "/api/payouts/draw-day"]);
    expect(posts.map(([, init]) => JSON.parse(String(init?.body)))).toEqual([{ draw_day: 15 }, { draw_day: null }]);
  });

  it("keeps the panel open with why a change was refused", async () => {
    server(NONE, "your roles don't allow owners' money");
    render(<DrawDayCard />);
    fireEvent.click(await screen.findByRole("button", { name: "Edit draw day" }));
    const panel = screen.getByRole("dialog", { name: "Owner draws" });
    fireEvent.click(within(panel).getByRole("button", { name: "Save" }));
    expect((await within(panel).findByRole("alert")).textContent).toBe("your roles don't allow owners' money");
  });
});
