import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { holdBack } from "../holdBack";
import type { Paged } from "../rentals";
import type { HistoryLine } from "./HistoryCard";
import { HistoryCard } from "./HistoryCard";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

const LINE: HistoryLine = {
  posted_on: "2025-03-04", type: "Payment", description: "Check 311", amount: "-1200.00",
  reference: "311", unit: "Apt 1", property: null,
};

function page(items: HistoryLine[], n = 1, total = items.length): Paged<HistoryLine> {
  return { items, total, page: n, per_page: 50 };
}

// GET /api/history answers `pages[n]` for ?page=n.
function server(pages: Record<number, Paged<HistoryLine>>) {
  const fetch = vi.fn(async (path: string) => {
    const n = Number(new URL(path, "http://pms.test").searchParams.get("page") ?? "1");
    return new Response(JSON.stringify(pages[n]), { status: 200 });
  });
  vi.stubGlobal("fetch", fetch);
  return fetch;
}

describe("HistoryCard", () => {
  it("lists a tenant's lines from before the switch with their unit, as the old system printed them", async () => {
    const fetch = server({ 1: page([LINE, { ...LINE, posted_on: "2025-03-01", type: "Charge", description: "March rent", amount: "1200.00", reference: null }]) });
    render(<HistoryCard about="tenant" id="t1" />);

    const table = await screen.findByRole("table", { name: "Before the switch" });
    const rows = within(table).getAllByRole("row").slice(1).map((r) => within(r).getAllByRole("cell").map((c) => c.textContent));
    expect(rows).toEqual([
      ["03/04/2025", "Apt 1", "Payment", "Check 311", "311", "($1,200.00)"],
      ["03/01/2025", "Apt 1", "Charge", "March rent", "", "$1,200.00"],
    ]);
    expect(screen.getByText(/none of it is in the books here/)).toBeTruthy();
    expect(String(fetch.mock.calls[0]![0])).toBe("/api/history?tenant_id=t1&page=1");
  });

  it("shows an owner's lines by property and turns the page", async () => {
    server({
      1: page([{ ...LINE, unit: null, property: "Property 1", description: "March draw" }], 1, 51),
      2: page([{ ...LINE, unit: null, property: "Property 1", description: "February draw" }], 2, 51),
    });
    render(<HistoryCard about="owner" id="o1" />);

    expect(await screen.findByRole("columnheader", { name: "Property" })).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Next" }));
    expect(await screen.findByText("February draw")).toBeTruthy();
  });

  it("shows the lines of the page turned to now, even when an earlier page's answer comes back last", async () => {
    const draw = (description: string, n: number) => page([{ ...LINE, unit: null, property: "Property 1", description }], n, 101);
    server({ 1: draw("March draw", 1), 2: draw("February draw", 2), 3: draw("January draw", 3) });
    const release = holdBack((path) => path.endsWith("page=3"));
    render(<HistoryCard about="owner" id="o1" />);
    await screen.findByText("March draw");
    fireEvent.click(screen.getByRole("button", { name: "Next" }));
    await screen.findByText("February draw");

    fireEvent.click(screen.getByRole("button", { name: "Next" }));
    fireEvent.click(screen.getByRole("button", { name: "Previous" }));
    await screen.findByText("March draw");
    await release();

    expect(screen.getByText("March draw")).toBeTruthy();
    expect(screen.queryByText("January draw")).toBeNull();
  });

  it("shows nothing for someone with no history, and a vendor's lines with no place column", async () => {
    server({ 1: page([]) });
    const { container } = render(<HistoryCard about="vendor" id="v1" />);
    await vi.waitFor(() => expect(container.innerHTML).toBe(""));
    cleanup();

    server({ 1: page([{ ...LINE, unit: null }]) });
    render(<HistoryCard about="vendor" id="v1" />);
    const table = await screen.findByRole("table", { name: "Before the switch" });
    expect(within(table).getAllByRole("columnheader").map((h) => h.textContent)).toEqual([
      "Date", "Type", "Description", "Reference", "Amount",
    ]);
  });
});
