import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { Me } from "../api";
import type { OwnerMoney } from "../payables";
import type { Portfolio } from "../setup";
import { OwnersPage, leasingFeeSaid } from "./OwnersPage";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

const ME: Me = {
  kind: "staff",
  email: "staff@example.com",
  name: "Staff 1",
  person_id: "s1",
  pmc_id: "p1",
  pmc_name: "Synthetic PMC",
};

const OWNERS: OwnerMoney[] = [
  {
    account: "a1", owner: "Owner 1", property: "Property 1", bank_account: "Operating",
    balance: "1200.00", reserve: "100.00", unpaid_bills: "300.00", available: "800.00",
    has_agreement: true,
  },
  {
    account: "a2", owner: "Owner 2", property: "Property 2", bank_account: "Operating",
    balance: "0.00", reserve: "0.00", unpaid_bills: "0.00", available: "0.00",
    has_agreement: false,
  },
];

const account = (n: number, property: string) => ({
  account: `a${n}`, owner_id: `o${n}`, owner: `Owner ${n}`, property_id: property,
  property: property === "pr1" ? "Property 1" : "Property 2", bank_account: "Operating",
  agreement: null,
});
const BOOKS: Portfolio = {
  bank_accounts: [],
  owners: [],
  properties: [
    {
      id: "pr1", name: "Property 1", late_fees: null, nsf_fee: null,
      units: [{ id: "u1", name: "Unit 1", leases: [{ id: "l1", starts_on: "2026-09-01", ends_on: null, monthly_rent: "1500.00", tenants: ["Tenant 1"] }] }],
    },
    {
      id: "pr2", name: "Property 2", late_fees: null, nsf_fee: null,
      units: [{ id: "u2", name: "Unit 2", leases: [{ id: "l2", starts_on: "2026-10-01", ends_on: null, monthly_rent: "900.00", tenants: ["Tenant 2"] }] }],
    },
  ],
  accounts: [account(1, "pr1"), account(3, "pr1"), account(2, "pr2")],
  tenants: [],
  vendors: [],
};

function server(answers: Record<string, unknown> = {}, refuse: Record<string, string> = {}) {
  const fetch = vi.fn(async (path: string, init?: RequestInit) => {
    if (init?.method !== "POST") {
      return new Response(JSON.stringify(path === "/api/setup" ? BOOKS : OWNERS), { status: 200 });
    }
    if (path in refuse) return new Response(JSON.stringify({ detail: refuse[path] }), { status: 409 });
    return new Response(JSON.stringify(answers[path] ?? {}), { status: 201 });
  });
  vi.stubGlobal("fetch", fetch);
  return fetch;
}

const posted = (fetch: ReturnType<typeof server>) =>
  fetch.mock.calls
    .filter(([, init]) => init?.method === "POST")
    .map(([path, init]) => [path, JSON.parse(String(init?.body))]);

const owner = (name: string) => screen.findByRole("listitem", { name });

describe("OwnersPage", () => {
  it("shows what each owner's account holds and may pay out", async () => {
    server();
    render(<OwnersPage me={ME} />);

    const first = await owner("Owner 1, Property 1");
    expect(within(first).getByTestId("available").textContent).toBe("$800.00 available");
    expect(first.textContent).toContain("$1,200.00 held, less a $100.00 reserve and $300.00 of unpaid bills");
    expect(first.textContent).not.toContain("No management agreement");
    const second = await owner("Owner 2, Property 2");
    expect(second.textContent).toContain("No management agreement: no reserve is kept and no fee is taken.");
  });

  it("records a draw of everything available once per request, then a contribution", async () => {
    const fetch = server({
      "/api/payables/draws": { amount: "800.00" },
      "/api/payables/contributions": { transfer_id: "t1" },
    });
    render(<OwnersPage me={ME} />);
    const first = await owner("Owner 1, Property 1");

    fireEvent.change(within(first).getByLabelText("Paid out on"), { target: { value: "2026-10-10" } });
    fireEvent.click(within(first).getByRole("button", { name: "Record draw" }));
    expect(await within(first).findByText("$800.00 paid out to Owner 1.")).toBeTruthy();
    fireEvent.change(within(first).getByLabelText("Draw amount (leave empty for all available)"), {
      target: { value: "50.00" },
    });
    fireEvent.click(within(first).getByRole("button", { name: "Record draw" }));
    await vi.waitFor(() => expect(posted(fetch)).toHaveLength(2));

    fireEvent.change(within(first).getByLabelText("Contribution received"), { target: { value: "250.00" } });
    fireEvent.change(within(first).getByLabelText("Contribution received on"), { target: { value: "2026-10-11" } });
    fireEvent.change(within(first).getByLabelText("For"), { target: { value: "Roof repair" } });
    fireEvent.click(within(first).getByRole("button", { name: "Record contribution" }));
    expect(await within(first).findByText("$250.00 from Owner 1 recorded.")).toBeTruthy();

    const [draw, again, contribution] = posted(fetch) as [string, Record<string, unknown>][];
    expect(draw?.[0]).toBe("/api/payables/draws");
    expect(draw?.[1]).toMatchObject({ account: "a1", amount: null, on: "2026-10-10" });
    expect(again?.[1]).toMatchObject({ account: "a1", amount: "50.00" });
    expect(again?.[1].request_id).not.toBe(draw?.[1].request_id);
    expect(contribution?.[0]).toBe("/api/payables/contributions");
    expect(contribution?.[1]).toMatchObject({
      account: "a1", amount: "250.00", on: "2026-10-11", memo: "Roof repair",
    });
  });

  it("shows why a draw was refused on its owner", async () => {
    server({}, { "/api/payables/draws": "0.00 is available to draw" });
    render(<OwnersPage me={ME} />);
    const second = await owner("Owner 2, Property 2");

    fireEvent.click(within(second).getByRole("button", { name: "Record draw" }));

    expect(await within(second).findByText("0.00 is available to draw")).toBeTruthy();
  });

  it("takes a period's management fees and lists each one, or why not", async () => {
    const fetch = server({
      "/api/payables/management-fees": [
        { account: "a1", owner: "Owner 1", property: "Property 1", fee: "150.00", refused: null },
        { account: "a2", owner: "Owner 2", property: "Property 2", fee: null, refused: "it would go below zero" },
      ],
    });
    render(<OwnersPage me={ME} />);
    await owner("Owner 1, Property 1");

    fireEvent.change(screen.getByLabelText("Period from"), { target: { value: "2026-09-01" } });
    fireEvent.change(screen.getByLabelText("Up to (not including)"), { target: { value: "2026-10-01" } });
    fireEvent.change(screen.getByLabelText("Taken on", { selector: "#fee-on" }), { target: { value: "2026-10-02" } });
    fireEvent.click(screen.getByRole("button", { name: "Take fees" }));

    const fees = await screen.findByRole("list", { name: "Fees taken" });
    expect(within(fees).getAllByRole("listitem").map((li) => li.textContent)).toEqual([
      "Owner 1, Property 1: $150.00 taken",
      "Owner 2, Property 2: not taken, it would go below zero",
    ]);
    expect(posted(fetch)).toEqual([
      ["/api/payables/management-fees", { period_start: "2026-09-01", period_end: "2026-10-01", on: "2026-10-02" }],
    ]);
  });

  it("says when no agreement was in force for a fee run", async () => {
    server({ "/api/payables/management-fees": [] });
    render(<OwnersPage me={ME} />);
    await owner("Owner 1, Property 1");

    fireEvent.click(screen.getByRole("button", { name: "Take fees" }));

    expect(await screen.findByText("No agreement was in force in that period.")).toBeTruthy();
  });

  it("takes a leasing fee from an owner of the lease's property", async () => {
    const fetch = server({ "/api/payables/leasing-fees": { amount: "750.00" } });
    render(<OwnersPage me={ME} />);
    await owner("Owner 1, Property 1");

    const from = screen.getByLabelText("From owner");
    expect(within(from).getAllByRole("option").map((o) => o.textContent)).toEqual(["Owner 1", "Owner 3"]);
    fireEvent.change(from, { target: { value: "a3" } });
    fireEvent.click(screen.getByRole("button", { name: "Take leasing fee" }));
    expect(await screen.findByText("A leasing fee of $750.00 is taken.")).toBeTruthy();

    fireEvent.change(screen.getByLabelText("Lease"), { target: { value: "l2" } });
    expect(within(from).getAllByRole("option").map((o) => o.textContent)).toEqual(["Owner 2"]);
    fireEvent.change(screen.getByLabelText("Taken on", { selector: "#leasing-on" }), {
      target: { value: "2026-10-03" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Take leasing fee" }));
    await vi.waitFor(() => expect(posted(fetch)).toHaveLength(2));

    expect(posted(fetch)[0]?.[1]).toMatchObject({ lease_id: "l1", account: "a3" });
    expect(posted(fetch)[1]?.[1]).toEqual({ lease_id: "l2", account: "a2", on: "2026-10-03" });
  });

  it("says when no owner has an account yet, and when the page can't be read", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (path: string) =>
        new Response(
          JSON.stringify(path === "/api/setup" ? { ...BOOKS, properties: [] } : []),
          { status: 200 },
        ),
      ),
    );
    render(<OwnersPage me={ME} />);
    expect(await screen.findByText("No owner has a property account yet. Add one in Setup.")).toBeTruthy();
    expect(screen.queryByText("Leasing fee")).toBeNull();
    cleanup();

    vi.stubGlobal(
      "fetch",
      vi.fn(async () => new Response(JSON.stringify({ detail: "sign in first" }), { status: 401 })),
    );
    render(<OwnersPage me={ME} />);
    expect((await screen.findAllByRole("alert"))[0]?.textContent).toBe("sign in first");
  });

  it("says so when the agreement takes no leasing fee, rather than that $0.00 was taken", () => {
    expect(leasingFeeSaid("750.00")).toBe("A leasing fee of $750.00 is taken.");
    expect(leasingFeeSaid("0.00")).toBe(
      "No leasing fee: the owner's agreement in force takes none, so none is due for this lease.",
    );
  });
});
