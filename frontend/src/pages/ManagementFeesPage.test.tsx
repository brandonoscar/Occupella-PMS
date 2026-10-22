import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { Me } from "../api";
import { holdBack } from "../holdBack";
import { lastMonth, usDate } from "../money";
import type { FeeDue } from "../payables";
import { ManagementFeesPage } from "./ManagementFeesPage";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

const ME: Me = {
  kind: "staff", email: "staff@example.com", name: "Staff 1", person_id: "s1", pmc_id: "p1", pmc_name: "Synthetic PMC",
};
const DUE: FeeDue[] = [
  { account: "a1", owner: "Owner 1", property: "Property 1", taken: false, fee: "80.00", refused: null },
  { account: "a2", owner: "Owner 2", property: "Property 2", taken: true, fee: "25.00", refused: null },
  { account: "a3", owner: "Owner 3", property: "Property 3", taken: false, fee: null,
    refused: "not enough money: the owner's account would be $25.00 short" },
];

function server(due: FeeDue[] | null = DUE, refuse = "", automatic = false) {
  const fetch = vi.fn(async (path: string, init?: RequestInit) => {
    if (path === "/api/payables/management-fees/automatic") {
      return new Response(JSON.stringify({ automatic, day_of_month: 5, changed_at: null, changed_by: null }), { status: 200 });
    }
    if (init?.method === "POST") {
      if (refuse) return new Response(JSON.stringify({ detail: refuse }), { status: 409 });
      return new Response(JSON.stringify([
        { account: "a1", owner: "Owner 1", property: "Property 1", fee: "80.00", refused: null, already: false },
        { account: "a2", owner: "Owner 2", property: "Property 2", fee: "25.00", refused: null, already: true },
        { account: "a3", owner: "Owner 3", property: "Property 3", fee: null, refused: "short", already: false },
      ]), { status: 200 });
    }
    if (due === null) return new Response(JSON.stringify({ detail: "a fee period ends after it starts" }), { status: 422 });
    return new Response(JSON.stringify(path.includes("period_start=2026-01-01") ? [] : due), { status: 200 });
  });
  vi.stubGlobal("fetch", fetch);
  return fetch;
}

describe("ManagementFeesPage", () => {
  it("says whether the fees are taken each month by themselves", async () => {
    server(DUE, "", true);
    render(<ManagementFeesPage me={ME} />);
    expect((await screen.findByTestId("automatic-fees")).textContent).toBe(
      "Taken by themselves: last month's, from the 5th of each month.",
    );
    cleanup();
    server(DUE, "", false);
    render(<ManagementFeesPage me={ME} />);
    expect((await screen.findByTestId("automatic-fees")).textContent).toBe(
      "Collected by hand on Management fees, or by themselves each month once turned on in Settings, Company setup.",
    );
  });

  it("shows each agreement's fee for the period, taken or to collect or why not, and collects them after asking", async () => {
    const fetch = server();
    render(<ManagementFeesPage me={ME} />);

    const table = await screen.findByRole("table", { name: "Management fees" });
    expect(within(table).getAllByRole("row").slice(1).map((r) => within(r).getAllByRole("cell").map((c) => c.textContent))).toEqual([
      ["Owner 1", "Property 1", "$80.00", "To collect"],
      ["Owner 2", "Property 2", "$25.00", "Collected"],
      ["Owner 3", "Property 3", "", "not enough money: the owner's account would be $25.00 short"],
    ]);
    // Last month by default, read by its last day; the API's period ends on this month's first.
    const month = lastMonth(new Date());
    const thisMonth = new Date(Date.UTC(new Date().getUTCFullYear(), new Date().getUTCMonth(), 1)).toISOString().slice(0, 10);
    expect((screen.getByLabelText("Period through") as HTMLInputElement).value).toBe(month.end);
    expect(String(fetch.mock.calls[0]?.[0])).toBe(
      `/api/payables/management-fees?period_start=${month.start}&period_end=${thisMonth}`,
    );
    expect(screen.queryByTestId("nothing-to-collect")).toBeNull();

    // The question names only what is taken now, and the period by its last day.
    fireEvent.click(screen.getByRole("button", { name: "Collect fees" }));
    const confirm = screen.getByRole("alertdialog");
    expect(confirm.textContent).toContain(
      `Collect $80.00 of management fees from 1 account for ${usDate(month.start)} to ${usDate(month.end)}? 1 was collected already.`,
    );
    fireEvent.click(within(confirm).getByRole("button", { name: "Confirm" }));

    // The fee taken before is not counted as taken now.
    expect(await screen.findByText("$80.00 collected from 1 account; 1 was collected already; 1 not taken: the list says why.")).toBeTruthy();
    const post = fetch.mock.calls.find(([, init]) => init?.method === "POST");
    expect(JSON.parse(String(post?.[1]?.body))).toMatchObject({ period_start: month.start, period_end: thisMonth });
  });

  it("offers no collection when every fee is collected already or of nothing, and says so", async () => {
    server([
      { account: "a1", owner: "Owner 1", property: "Property 1", taken: true, fee: "120.00", refused: null },
      { account: "a2", owner: "Owner 2", property: "Property 2", taken: false, fee: "0.00", refused: null },
    ]);
    render(<ManagementFeesPage me={ME} />);

    const table = await screen.findByRole("table", { name: "Management fees" });
    expect(within(table).getAllByRole("row").slice(1).map((r) => within(r).getAllByRole("cell").map((c) => c.textContent))).toEqual([
      ["Owner 1", "Property 1", "$120.00", "Collected"],
      ["Owner 2", "Property 2", "$0.00", "Nothing to collect"],
    ]);
    const month = lastMonth(new Date());
    expect(screen.getByTestId("nothing-to-collect").textContent).toBe(
      `Nothing to collect for ${usDate(month.start)} to ${usDate(month.end)}.`,
    );
    expect(screen.getByRole("button", { name: "Collect fees" })).toHaveProperty("disabled", true);
  });

  it("asks again for another period, and says when no agreement was in force", async () => {
    server();
    render(<ManagementFeesPage me={ME} />);
    await screen.findByRole("table", { name: "Management fees" });
    fireEvent.change(screen.getByLabelText("Period from"), { target: { value: "2026-01-01" } });

    expect((await screen.findByTestId("empty-state")).textContent).toContain("No management agreement in force for this period.");
    expect(screen.getByRole("button", { name: "Collect fees" })).toHaveProperty("disabled", true);
  });

  it("says why a period can't be read, and why collecting was refused", async () => {
    server(null);
    render(<ManagementFeesPage me={ME} />);
    expect((await screen.findByRole("alert")).textContent).toBe("a fee period ends after it starts");
    cleanup();

    server(DUE, "trust bank account is reconciled and closed");
    render(<ManagementFeesPage me={ME} />);
    await screen.findByRole("table", { name: "Management fees" });
    fireEvent.click(screen.getByRole("button", { name: "Collect fees" }));
    fireEvent.click(within(screen.getByRole("alertdialog")).getByRole("button", { name: "Confirm" }));
    expect((await screen.findByRole("alert")).textContent).toBe("trust bank account is reconciled and closed");
  });

  it("shows the fees of the period picked now, even when an earlier period's answer comes back last", async () => {
    server();
    const release = holdBack((path) => path.includes("period_start=2020-02-01"));
    render(<ManagementFeesPage me={ME} />);
    await screen.findByRole("table", { name: "Management fees" });

    fireEvent.change(screen.getByLabelText("Period from"), { target: { value: "2020-02-01" } });
    fireEvent.change(screen.getByLabelText("Period from"), { target: { value: "2026-01-01" } });
    await screen.findByTestId("empty-state");
    await release();

    expect(screen.getByTestId("empty-state").textContent).toContain("No management agreement in force for this period.");
    expect(screen.queryByRole("table", { name: "Management fees" })).toBeNull();
  });
});
