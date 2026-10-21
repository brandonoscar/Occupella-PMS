import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { YearClose } from "./YearCloseCard";
import { YearCloseCard } from "./YearCloseCard";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

const OPEN: YearClose = {
  closed: [],
  closed_until: null,
  next: {
    year: 2025,
    accounts: [
      { id: "b1", name: "Operating trust account", approved_through: "2025-11-30", in_the_year: true, ready: false },
      { id: "b2", name: "Deposits trust account", approved_through: null, in_the_year: false, ready: true },
    ],
    waiting_entries: 2,
    ready: false,
  },
};

const READY: YearClose = {
  ...OPEN,
  next: {
    ...OPEN.next!,
    accounts: [{ ...OPEN.next!.accounts[0]!, approved_through: "2025-12-31", ready: true }, OPEN.next!.accounts[1]!],
    waiting_entries: 0,
    ready: true,
  },
};

const CLOSED: YearClose = {
  closed: [{ year: 2025, closed_on: "2026-10-21", closed_by: "Demo Manager" }],
  closed_until: "2025-12-31",
  next: null,
};

// GET /api/year-close answers each of `states` in turn; a POST answers 201, or `refused`.
function server(states: YearClose[], refused?: string) {
  let n = 0;
  const fetch = vi.fn(async (_path: string, init?: RequestInit) => {
    if (init?.method === "POST") {
      if (refused) return new Response(JSON.stringify({ detail: refused }), { status: 409 });
      return new Response(JSON.stringify(CLOSED.closed[0]), { status: 201 });
    }
    const state = states[Math.min(n, states.length - 1)];
    n += 1;
    return new Response(JSON.stringify(state), { status: 200 });
  });
  vi.stubGlobal("fetch", fetch);
  return fetch;
}

describe("YearCloseCard", () => {
  it("says what holds last year back, and Close waits until nothing does", async () => {
    server([OPEN]);
    render(<YearCloseCard />);

    const ready = await screen.findByRole("table", { name: "Ready to close 2025" });
    expect(within(ready).getByRole("row", { name: "Operating trust account, 2025" }).textContent).toContain(
      "11/30/2025Approve December",
    );
    expect(within(ready).getByRole("row", { name: "Deposits trust account, 2025" }).textContent).toContain(
      "Not yetNot in 2025",
    );
    expect(screen.getByTestId("waiting-entries").textContent).toContain(
      "2 journal entries dated in 2025 or before wait for a decision",
    );
    expect(screen.getByRole("link", { name: "post or decline" }).getAttribute("href")).toBe("/journal");
    expect((screen.getByRole("button", { name: "Close 2025" }) as HTMLButtonElement).disabled).toBe(true);
  });

  it("closes a ready year once staff confirm, then shows it closed", async () => {
    const fetch = server([READY, CLOSED]);
    render(<YearCloseCard />);

    fireEvent.click(await screen.findByRole("button", { name: "Close 2025" }));
    const asked = screen.getByRole("alertdialog");
    expect(asked.textContent).toContain("can't be reopened");
    fireEvent.click(within(asked).getByRole("button", { name: "Close 2025" }));

    expect(await screen.findByTestId("closed-until")).toHaveProperty(
      "textContent",
      "Closed through 12/31/2025: nothing dated then or before is charged or billed.",
    );
    expect(screen.getByRole("list", { name: "Years closed" }).textContent).toBe(
      "2025, closed on 10/21/2026 by Demo Manager",
    );
    expect(screen.getByText("Nothing more to close until this year ends.")).toBeTruthy();
    const posts = fetch.mock.calls.filter(([, init]) => init?.method === "POST");
    expect(posts.map(([path, init]) => [path, JSON.parse(String(init?.body))])).toEqual([
      ["/api/year-close", { year: 2025 }],
    ]);
  });

  it("says why a close was refused", async () => {
    server([READY], "approve month-end through 12/31/2025 first: Operating trust account");
    render(<YearCloseCard />);

    fireEvent.click(await screen.findByRole("button", { name: "Close 2025" }));
    fireEvent.click(within(screen.getByRole("alertdialog")).getByRole("button", { name: "Close 2025" }));

    expect(await screen.findByText("approve month-end through 12/31/2025 first: Operating trust account")).toBeTruthy();
  });
});
