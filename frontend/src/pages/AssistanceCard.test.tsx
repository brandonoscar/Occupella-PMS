import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { AssistanceTerms, Authority, AuthorityCharge, LeaseAssistance } from "../assistance";
import { holdBack } from "../holdBack";
import { AssistanceCard } from "./AssistanceCard";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

const AUTHORITIES: Authority[] = [{ id: "h1", name: "Synthetic Housing Authority", leases: 1, owed: "450.00" }];
const CHARGE: AuthorityCharge = {
  id: "c1", lease_id: "l1", authority_id: "h1", due_on: "2026-10-01", memo: "Rent due 2026-10-01", source: "rent",
  charged: "800.00", adjusted: "-50.00", paid: "300.00", open: "450.00",
};
const TERMS: AssistanceTerms = {
  id: "t1", authority_id: "h1", authority: "Synthetic Housing Authority", starts_on: "2026-10-01", amount: "800.00",
  contract: "HAP-0001", recorded_by: "Staff 1",
};
const ASSISTED: LeaseAssistance = {
  in_force: TERMS, monthly_rent: "1000.00", tenant_part: "200.00", terms: [TERMS], charges: [CHARGE], owed: "450.00",
};

function server(assistance: LeaseAssistance = ASSISTED, authorities: Authority[] = AUTHORITIES, refuse = "") {
  const fetch = vi.fn(async (path: string, init?: RequestInit) => {
    if (init?.method === "POST") {
      if (refuse) return new Response(JSON.stringify({ detail: refuse }), { status: 409 });
      return new Response(JSON.stringify({ id: "new" }), { status: 201 });
    }
    if (path === "/api/assistance/authorities") return new Response(JSON.stringify(authorities), { status: 200 });
    return new Response(JSON.stringify(assistance), { status: 200 });
  });
  vi.stubGlobal("fetch", fetch);
  return fetch;
}

const posted = (fetch: ReturnType<typeof server>) =>
  fetch.mock.calls.filter(([, init]) => init?.method === "POST").map(([path, init]) => [path, JSON.parse(String(init?.body))]);

function open(name: string) {
  fireEvent.click(screen.getByRole("button", { name }));
  return screen.getByRole("dialog");
}

describe("AssistanceCard", () => {
  it("shows how the rent splits, each assistance and what the authority owes", async () => {
    server();
    render(<AssistanceCard leaseId="l1" unit="Unit 1" />);

    expect((await screen.findByTestId("assistance-split")).textContent).toBe(
      "Synthetic Housing Authority pays $800.00 of the $1,000.00 rent; the tenant pays $200.00.",
    );
    expect(screen.getByRole("list", { name: "Housing assistance, Unit 1" }).textContent).toBe(
      "From 10/01/2026: Synthetic Housing Authority pays $800.00 a month (contract HAP-0001)",
    );
    const row = screen.getByRole("row", { name: "Rent due 2026-10-01, 10/01/2026" });
    expect(within(row).getAllByRole("cell").slice(0, 5).map((c) => c.textContent)).toEqual([
      "10/01/2026", "Rent due 2026-10-01", "$750.00", "$300.00", "$450.00",
    ]);
    expect(screen.getByTestId("authority-owes").textContent).toBe("The housing authority owes $450.00 for this lease.");
  });

  it("shows the assistance of the lease opened now, even when an earlier lease's answer comes back last", async () => {
    const other: LeaseAssistance = { ...ASSISTED, monthly_rent: "1200.00", tenant_part: "400.00" };
    vi.stubGlobal("fetch", vi.fn(async (path: string) => new Response(JSON.stringify(
      path === "/api/assistance/authorities" ? AUTHORITIES : path.endsWith("/l1") ? ASSISTED : other,
    ))));
    const release = holdBack((path) => path === "/api/assistance/leases/l1");
    const { rerender } = render(<AssistanceCard leaseId="l1" unit="Unit 1" />);
    rerender(<AssistanceCard leaseId="l2" unit="Unit 2" />);
    const split = "Synthetic Housing Authority pays $800.00 of the $1,200.00 rent; the tenant pays $400.00.";
    await vi.waitFor(() => expect(screen.getByTestId("assistance-split").textContent).toBe(split));
    await release();

    expect(screen.getByTestId("assistance-split").textContent).toBe(split);
  });

  it("sets assistance from the first of a month", async () => {
    const fetch = server();
    render(<AssistanceCard leaseId="l1" unit="Unit 1" />);
    await screen.findByTestId("assistance-split");

    const form = open("Set housing assistance");
    fireEvent.change(within(form).getByLabelText("From (the first of a month)"), { target: { value: "2026-12-01" } });
    fireEvent.change(within(form).getByLabelText("The authority pays each month"), { target: { value: "850.00" } });
    fireEvent.change(within(form).getByLabelText("HAP contract (optional)"), { target: { value: " HAP-0002 " } });
    fireEvent.click(within(form).getByRole("button", { name: "Save assistance" }));

    expect(await screen.findByText("The housing authority pays $850.00 a month from 12/01/2026.")).toBeTruthy();
    expect(posted(fetch)).toEqual([[
      "/api/assistance/leases/l1/terms",
      { housing_authority_id: "h1", starts_on: "2026-12-01", amount: "850.00", contract: "HAP-0002" },
    ]]);
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("ends assistance with 0, and says so", async () => {
    const fetch = server();
    render(<AssistanceCard leaseId="l1" unit="Unit 1" />);
    await screen.findByTestId("assistance-split");

    const form = open("Set housing assistance");
    fireEvent.change(within(form).getByLabelText("From (the first of a month)"), { target: { value: "2027-01-01" } });
    fireEvent.change(within(form).getByLabelText("The authority pays each month"), { target: { value: "0" } });
    fireEvent.click(within(form).getByRole("button", { name: "Save assistance" }));

    expect(await screen.findByText("Housing assistance ends from 01/01/2027.")).toBeTruthy();
    expect(posted(fetch)).toEqual([["/api/assistance/leases/l1/terms", expect.objectContaining({ amount: "0", contract: null })]]);
  });

  it("charges the authority by hand, and credits off one of its charges", async () => {
    const fetch = server();
    render(<AssistanceCard leaseId="l1" unit="Unit 1" />);
    await screen.findByTestId("assistance-split");

    let form = open("Charge the authority");
    fireEvent.change(within(form).getByLabelText("Amount"), { target: { value: "800.00" } });
    fireEvent.change(within(form).getByLabelText("Due on"), { target: { value: "2026-09-01" } });
    fireEvent.change(within(form).getByLabelText("What it is for"), { target: { value: "September's part" } });
    fireEvent.click(within(form).getByRole("button", { name: "Charge the authority" }));
    expect(await screen.findByText("$800.00 charged to the housing authority.")).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: "Actions for Rent due 2026-10-01" }));
    fireEvent.click(screen.getByRole("menuitem", { name: "Credit the authority" }));
    form = screen.getByRole("dialog", { name: "Credit the housing authority" });
    expect(within(form).getByTestId("credit-of").textContent).toContain("Rent due 2026-10-01, due 10/01/2026: $450.00 open.");
    fireEvent.change(within(form).getByLabelText("Amount"), { target: { value: "100.00" } });
    fireEvent.change(within(form).getByLabelText("Why"), { target: { value: "Abatement" } });
    fireEvent.click(within(form).getByRole("button", { name: "Credit the authority" }));
    expect(await screen.findByText("$100.00 credited off Rent due 2026-10-01.")).toBeTruthy();

    expect(posted(fetch)).toEqual([
      ["/api/assistance/leases/l1/charges", {
        kind: "charge", housing_authority_id: "h1", amount: "800.00", due_on: "2026-09-01", memo: "September's part",
      }],
      ["/api/assistance/leases/l1/charges", expect.objectContaining({
        kind: "credit", charge_id: "c1", amount: "100.00", memo: "Abatement",
      })],
    ]);
  });

  it("asks for a housing authority first, and shows a refusal in the panel", async () => {
    server({ ...ASSISTED, in_force: null, terms: [], charges: [], owed: "0.00" }, []);
    render(<AssistanceCard leaseId="l1" unit="Unit 1" />);
    expect((await screen.findByTestId("assistance-split")).textContent).toBe("The tenant pays all of the $1,000.00 rent.");
    expect(screen.queryByTestId("authority-owes")).toBeNull();

    const form = open("Set housing assistance");
    expect(within(form).getByText("Add the housing authority first, under Rentals, Housing authorities.")).toBeTruthy();
    expect((within(form).getByRole("button", { name: "Save assistance" }) as HTMLButtonElement).disabled).toBe(true);
    fireEvent.click(within(form).getByRole("button", { name: "Close" }));

    const charge = open("Charge the authority");
    expect((within(charge).getByRole("button", { name: "Charge the authority" }) as HTMLButtonElement).disabled).toBe(true);
  });

  it("shows the database's refusal in the panel", async () => {
    server(ASSISTED, AUTHORITIES, "rent due 2026-10-01 is charged already; housing assistance starts after its month");
    render(<AssistanceCard leaseId="l1" unit="Unit 1" />);
    await screen.findByTestId("assistance-split");

    const form = open("Set housing assistance");
    fireEvent.change(within(form).getByLabelText("The authority pays each month"), { target: { value: "800.00" } });
    fireEvent.click(within(form).getByRole("button", { name: "Save assistance" }));

    expect(await within(form).findByText(/is charged already/)).toBeTruthy();
  });

  it("says why the assistance didn't load", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({ detail: "no such lease" }), { status: 404 })));
    render(<AssistanceCard leaseId="l1" unit="Unit 1" />);
    expect(await screen.findByText("no such lease")).toBeTruthy();
  });
});
