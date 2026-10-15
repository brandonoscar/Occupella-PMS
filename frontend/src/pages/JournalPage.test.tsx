import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { Me } from "../api";
import type { Journal, JournalEntry } from "../journal";
import { JournalPage } from "./JournalPage";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

const ME: Me = {
  kind: "staff",
  email: "manager@example.com",
  name: "Manager 1",
  person_id: "s2",
  pmc_id: "p1",
  pmc_name: "Synthetic PMC",
  roles: ["approve"],
};

const ACCOUNTS = [
  { id: "cash", kind: "bank_cash", bank: "Operating", label: "Operating: cash" },
  { id: "o1", kind: "owner_property", bank: "Operating", label: "Operating: Owner 1 / Property 1" },
  { id: "o2", kind: "owner_property", bank: "Operating", label: "Operating: Owner 2 / Property 2" },
];

const entry = (over: Partial<JournalEntry>): JournalEntry => ({
  id: "e1",
  event_on: "2026-02-10",
  memo: "Rent deposited to the wrong property",
  proposed_by: "Bookkeeper 1",
  proposed_by_me: false,
  proposed_at: "2026-02-10T15:00:00Z",
  lines: [{ from_account: "o1", to_account: "o2", amount: "25.00" }],
  status: "waiting",
  decided_by: null,
  decided_at: null,
  reason: null,
  ...over,
});

const JOURNAL: Journal = {
  may_post: true,
  accounts: ACCOUNTS,
  entries: [
    entry({}),
    entry({ id: "e2", memo: "My own correction", proposed_by: "Manager 1", proposed_by_me: true }),
    entry({ id: "e3", memo: "Fee taken twice", status: "posted", decided_by: "Manager 1" }),
    entry({ id: "e4", memo: "Entered twice", status: "declined", decided_by: "Manager 1", reason: "Duplicate" }),
  ],
};

function server(journal: Journal = JOURNAL, refuse: Record<string, string> = {}) {
  const fetch = vi.fn(async (path: string, init?: RequestInit) => {
    if (init?.method !== "POST") return new Response(JSON.stringify(journal), { status: 200 });
    if (path in refuse) return new Response(JSON.stringify({ detail: refuse[path] }), { status: 409 });
    if (path.endsWith("/decline")) return new Response(null, { status: 204 });
    if (path.endsWith("/post")) return new Response(JSON.stringify({ transfers: 1 }), { status: 200 });
    return new Response(JSON.stringify({ id: "e9" }), { status: 201 });
  });
  vi.stubGlobal("fetch", fetch);
  return fetch;
}

const posted = (fetch: ReturnType<typeof server>) =>
  fetch.mock.calls
    .filter(([, init]) => init?.method === "POST")
    .map(([path, init]) => [path, JSON.parse(String(init?.body))]);

const row = (name: string) => screen.getByRole("listitem", { name });

describe("JournalPage", () => {
  it("lists entries with their lines in words, and posts one another staff member proposed", async () => {
    const fetch = server();
    render(<JournalPage me={ME} />);
    const waiting = await screen.findByRole("listitem", { name: "2026-02-10 Rent deposited to the wrong property" });

    expect(within(waiting).getByTestId("status").textContent).toBe("waiting");
    expect(within(waiting).getByRole("row", { name: "Operating: Owner 1 / Property 1 Operating: Owner 2 / Property 2 $25.00" })).toBeTruthy();
    // Whoever proposed an entry doesn't post it: the page says so where the button would be.
    const mine = row("2026-02-10 My own correction");
    expect(within(mine).queryByRole("button", { name: "Post" })).toBeNull();
    expect(mine.textContent).toContain("You proposed it: another staff member who may approve posts it.");
    expect(row("2026-02-10 Fee taken twice").textContent).toContain("Posted by Manager 1.");
    expect(row("2026-02-10 Entered twice").textContent).toContain("Declined by Manager 1: Duplicate.");
    expect(within(row("2026-02-10 Entered twice")).queryByRole("button")).toBeNull();

    fireEvent.click(within(waiting).getByRole("button", { name: "Post" }));
    expect(await screen.findByText("Posted: Rent deposited to the wrong property.")).toBeTruthy();
    expect(posted(fetch)).toEqual([["/api/journal/e1/post", {}]]);
  });

  it("proposes an entry of several lines, and clears the form", async () => {
    const fetch = server();
    render(<JournalPage me={ME} />);
    await screen.findByLabelText("Line 1 from");

    fireEvent.change(screen.getByLabelText("Dated"), { target: { value: "2026-03-01" } });
    fireEvent.change(screen.getByLabelText("What it corrects"), { target: { value: "Deposit held in operating" } });
    fireEvent.change(screen.getByLabelText("Line 1 from"), { target: { value: "o1" } });
    fireEvent.change(screen.getByLabelText("Line 1 to"), { target: { value: "cash" } });
    fireEvent.change(screen.getByLabelText("Line 1 amount"), { target: { value: "300.00" } });
    fireEvent.click(screen.getByRole("button", { name: "Add a line" }));
    fireEvent.click(screen.getByRole("button", { name: "Add a line" }));
    fireEvent.click(screen.getByRole("button", { name: "Remove line 3" }));
    fireEvent.change(screen.getByLabelText("Line 2 amount"), { target: { value: "1.50" } });
    fireEvent.click(screen.getByRole("button", { name: "Propose entry" }));

    expect(
      await screen.findByText("Proposed: Deposit held in operating. Another staff member who may approve posts it."),
    ).toBeTruthy();
    expect(posted(fetch)).toEqual([
      [
        "/api/journal",
        {
          event_on: "2026-03-01",
          memo: "Deposit held in operating",
          lines: [
            { from_account: "o1", to_account: "cash", amount: "300.00" },
            { from_account: "cash", to_account: "o1", amount: "1.50" },
          ],
        },
      ],
    ]);
    expect((screen.getByLabelText("What it corrects") as HTMLInputElement).value).toBe("");
    expect(screen.queryByLabelText("Line 2 amount")).toBeNull();
  });

  it("declines an entry with why, and shows a refusal where it happened", async () => {
    const fetch = server(JOURNAL, {
      "/api/journal/e1/post": "not enough money: the owner's account would be $0.01 short",
    });
    render(<JournalPage me={ME} />);
    const waiting = await screen.findByRole("listitem", { name: "2026-02-10 Rent deposited to the wrong property" });

    fireEvent.click(within(waiting).getByRole("button", { name: "Post" }));
    expect((await within(waiting).findByRole("alert")).textContent).toBe(
      "not enough money: the owner's account would be $0.01 short",
    );

    fireEvent.click(within(waiting).getByRole("button", { name: "Decline" }));
    fireEvent.change(within(waiting).getByLabelText("Why"), { target: { value: "Too much" } });
    fireEvent.click(within(waiting).getByRole("button", { name: "Decline entry" }));
    expect(await screen.findByText("Declined: Rent deposited to the wrong property.")).toBeTruthy();
    expect(posted(fetch).at(-1)).toEqual(["/api/journal/e1/decline", { reason: "Too much" }]);
  });

  it("says who may post, and shows an empty journal", async () => {
    server({ ...JOURNAL, may_post: false, entries: [entry({})] });
    render(<JournalPage me={{ ...ME, roles: [] }} />);
    const waiting = await screen.findByRole("listitem", { name: "2026-02-10 Rent deposited to the wrong property" });
    expect(within(waiting).queryByRole("button", { name: "Post" })).toBeNull();
    expect(waiting.textContent).toContain("A staff member who may approve posts it.");
    cleanup();

    server({ ...JOURNAL, entries: [] });
    render(<JournalPage me={ME} />);
    expect((await screen.findByTestId("empty-state")).textContent).toContain("No journal entries yet");
  });

  it("asks for accounts first when the company has fewer than two", async () => {
    server({ ...JOURNAL, accounts: [ACCOUNTS[0]!], entries: [] });
    render(<JournalPage me={ME} />);
    expect(await screen.findByText("Open a trust bank account and the accounts it holds in Setup first.")).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Propose entry" })).toBeNull();
  });
});
