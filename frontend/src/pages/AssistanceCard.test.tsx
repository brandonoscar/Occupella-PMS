import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { AssistanceTerms, Authority, AuthorityCharge, Compliance, LeaseAssistance } from "../assistance";
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
const COMPLIANCE: Compliance = {
  recertify_by: "2026-11-30", recertification: "due within 120 days", inspect_by: "2026-10-15",
  inspection: "failed, reinspection overdue",
  certifications: [{
    id: "r1", kind: "move-in", effective_on: "2025-11-30", household_size: 3, annual_income: "18000.00",
    next_due_on: "2026-11-30", recorded_by: "Staff 1",
  }],
  inspections: [{
    id: "i1", authority_id: "h1", authority: "Synthetic Housing Authority", inspected_on: "2026-10-01", result: "failed",
    due_on: "2026-10-15", recorded_by: "Staff 1",
  }],
};
const ASSISTED: LeaseAssistance = {
  in_force: TERMS, monthly_rent: "1000.00", tenant_part: "200.00", terms: [TERMS], charges: [CHARGE], owed: "450.00",
  compliance: COMPLIANCE,
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

  it("says where the certification and inspection stand and lists each one", async () => {
    server();
    render(<AssistanceCard leaseId="l1" unit="Unit 1" />);

    expect((await screen.findByTestId("recertification")).textContent).toBe("due within 120 daysRecertify by 11/30/2026");
    expect(screen.getByTestId("inspection").textContent).toBe("failed, reinspection overdueInspect by 10/15/2026");
    const rows = (name: string) => within(screen.getByRole("table", { name })).getAllByRole("row").slice(1)
      .map((r) => within(r).getAllByRole("cell").map((c) => c.textContent));
    expect(rows("Income certifications")).toEqual([["11/30/2025", "Move-in", "3", "$18,000.00", "11/30/2026"]]);
    expect(rows("Inspections")).toEqual([["10/01/2026", "failed", "10/15/2026", "Synthetic Housing Authority"]]);
  });

  it("records a certification, its due day left to the PMS unless given", async () => {
    const fetch = server();
    render(<AssistanceCard leaseId="l1" unit="Unit 1" />);
    await screen.findByTestId("recertification");

    const form = open("Record certification");
    fireEvent.change(within(form).getByLabelText("Kind"), { target: { value: "interim" } });
    fireEvent.change(within(form).getByLabelText("Takes effect"), { target: { value: "2026-10-01" } });
    fireEvent.change(within(form).getByLabelText("People in the household"), { target: { value: "4" } });
    fireEvent.change(within(form).getByLabelText("Annual income"), { target: { value: "12000.00" } });
    fireEvent.click(within(form).getByRole("button", { name: "Record certification" }));

    expect(await screen.findByText("Interim certification from 10/01/2026 recorded.")).toBeTruthy();
    expect(posted(fetch)).toEqual([[
      "/api/assistance/leases/l1/certifications",
      { kind: "interim", effective_on: "2026-10-01", household_size: 4, annual_income: "12000.00", next_due_on: null },
    ]]);
  });

  it("records an inspection: passed, its next due day optional; failed, a reinspection day required", async () => {
    const fetch = server();
    render(<AssistanceCard leaseId="l1" unit="Unit 1" />);
    await screen.findByTestId("inspection");

    let form = open("Record inspection");
    fireEvent.change(within(form).getByLabelText("Inspected on"), { target: { value: "2026-10-12" } });
    expect((within(form).getByLabelText("Next inspection due (optional)") as HTMLInputElement).required).toBe(false);
    fireEvent.click(within(form).getByRole("button", { name: "Record inspection" }));
    expect(await screen.findByText("Inspection on 10/12/2026 recorded: passed.")).toBeTruthy();

    form = open("Record inspection");
    fireEvent.change(within(form).getByLabelText("Inspected on"), { target: { value: "2026-10-12" } });
    fireEvent.change(within(form).getByLabelText("Result"), { target: { value: "failed" } });
    const by = within(form).getByLabelText("Must pass a reinspection by") as HTMLInputElement;
    expect(by.required).toBe(true);
    fireEvent.change(by, { target: { value: "2026-11-12" } });
    fireEvent.click(within(form).getByRole("button", { name: "Record inspection" }));
    expect(await screen.findByText("Inspection on 10/12/2026 recorded: failed.")).toBeTruthy();

    expect(posted(fetch)).toEqual([
      ["/api/assistance/leases/l1/inspections", { inspected_on: "2026-10-12", result: "passed", due_on: null }],
      ["/api/assistance/leases/l1/inspections", { inspected_on: "2026-10-12", result: "failed", due_on: "2026-11-12" }],
    ]);
  });

  it("asks for a housing authority first, and shows a refusal in the panel", async () => {
    server({ ...ASSISTED, in_force: null, terms: [], charges: [], owed: "0.00" }, []);
    render(<AssistanceCard leaseId="l1" unit="Unit 1" />);
    expect((await screen.findByTestId("assistance-split")).textContent).toBe("The tenant pays all of the $1,000.00 rent.");
    expect(screen.queryByTestId("authority-owes")).toBeNull();
    // No assistance on file: nothing to certify or inspect for.
    expect(screen.queryByTestId("recertification")).toBeNull();
    expect(screen.queryByRole("button", { name: "Record certification" })).toBeNull();

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
