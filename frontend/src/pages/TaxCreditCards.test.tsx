import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { LeaseAssistance } from "../assistance";
import type { LeaseTaxCredit, TaxCreditLimits, UnitTaxCredit } from "../taxCredit";
import { TaxCreditLeaseCard, TaxCreditLimitsCard, TaxCreditUnitCard } from "./TaxCreditCards";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

const LIMITS: TaxCreditLimits[] = [
  { starts_on: "2026-04-01", limits: ["30000", "34300", "38600", "42850", "46300", "49750", "53150", "56600"] },
];
const DESIGNATED = { starts_on: "2026-01-01", income_percent: 60, bedrooms: 1, utility_allowance: "50.00" };
const UNIT: UnitTaxCredit = { in_force: DESIGNATED, max_gross_rent: "964", designations: [DESIGNATED] };
const STANDING: LeaseTaxCredit = {
  income_percent: 60, bedrooms: 1, certification: "current", income_test: "over 140%: next available unit rule",
  max_gross_rent: "964", gross_rent: "1014.00", rent_test: "over by 50.00",
};
const ASSISTANCE = {
  in_force: null, monthly_rent: "964.00", tenant_part: "964.00", terms: [], charges: [], owed: "0.00",
  compliance: {
    recertify_by: null, recertification: "none on file", inspect_by: null, inspection: "none on file",
    certifications: [{
      id: "r1", kind: "move-in", effective_on: "2026-01-01", household_size: 2, annual_income: "40000.00",
      next_due_on: "2027-01-01", recorded_by: "Staff 1",
    }],
    inspections: [],
  },
} satisfies LeaseAssistance;

function server(answers: Record<string, unknown>, refuse = "") {
  const fetch = vi.fn(async (path: string, init?: RequestInit) => {
    if (init?.method === "POST") {
      if (refuse) return new Response(JSON.stringify({ detail: refuse }), { status: 409 });
      return new Response(JSON.stringify({ id: "new" }), { status: 201 });
    }
    return new Response(JSON.stringify(path in answers ? answers[path] : { detail: "not found" }),
      { status: path in answers ? 200 : 404 });
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

describe("TaxCreditLimitsCard", () => {
  it("lists a property's limits and sets new ones from a day", async () => {
    const fetch = server({ "/api/tax-credit/properties/p1": LIMITS });
    render(<TaxCreditLimitsCard propertyId="p1" />);

    const row = await screen.findByRole("row", { name: "From 04/01/2026" });
    expect(within(row).getAllByRole("cell").map((c) => c.textContent)).toEqual([
      "04/01/2026", "$30,000.00", "$34,300.00", "$38,600.00", "$42,850.00", "$46,300.00", "$49,750.00",
      "$53,150.00", "$56,600.00",
    ]);

    const form = open("Set income limits");
    fireEvent.change(within(form).getByLabelText("From"), { target: { value: "2027-04-01" } });
    ["31000", "35400", "39800", "44200", "47750", "51300", "54850", "58350"].forEach((value, n) =>
      fireEvent.change(within(form).getByLabelText(n === 0 ? "1 person" : `${n + 1} people`), { target: { value } }));
    fireEvent.click(within(form).getByRole("button", { name: "Save income limits" }));

    expect(await screen.findByText("Income limits from 04/01/2027 saved.")).toBeTruthy();
    expect(posted(fetch)).toEqual([[
      "/api/tax-credit/limits",
      {
        property_id: "p1", starts_on: "2027-04-01",
        limits: ["31000", "35400", "39800", "44200", "47750", "51300", "54850", "58350"],
      },
    ]]);
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("says when none are on file, and why a save was refused", async () => {
    server({ "/api/tax-credit/properties/p1": [] }, "this property's income limits change on that day already");
    render(<TaxCreditLimitsCard propertyId="p1" />);

    expect((await screen.findByTestId("no-income-limits")).textContent).toContain("No income limits on file.");
    const form = open("Set income limits");
    for (let n = 0; n < 8; n++) {
      fireEvent.change(within(form).getByLabelText(n === 0 ? "1 person" : `${n + 1} people`), { target: { value: "1" } });
    }
    fireEvent.click(within(form).getByRole("button", { name: "Save income limits" }));
    expect((await within(form).findByRole("alert")).textContent).toContain("change on that day already");
  });
});

describe("TaxCreditUnitCard", () => {
  it("shows a unit's designation and the most its gross rent may be", async () => {
    server({ "/api/tax-credit/units/u1": UNIT });
    render(<TaxCreditUnitCard unitId="u1" />);

    expect((await screen.findByTestId("tax-credit-unit")).textContent).toBe(
      "A tax credit unit at 60% of median, 1 bedroom. The rent and the $50.00 utility allowance may come to $964.00 a month.",
    );
    expect(screen.getByRole("list", { name: "Tax credit designations" }).textContent).toBe(
      "From 01/01/2026: 60% of median, 1 bedroom, utility allowance $50.00",
    );
  });

  it("says a unit is not one, or waits for the property's limits", async () => {
    server({ "/api/tax-credit/units/u1": { in_force: null, max_gross_rent: null, designations: [] } });
    render(<TaxCreditUnitCard unitId="u1" />);
    expect((await screen.findByTestId("tax-credit-unit")).textContent).toBe("Not a tax credit unit.");
    cleanup();

    server({ "/api/tax-credit/units/u1": { ...UNIT, max_gross_rent: null } });
    render(<TaxCreditUnitCard unitId="u1" />);
    expect((await screen.findByTestId("tax-credit-unit")).textContent).toContain(
      "Its rent limit waits for the property's income limits.",
    );
  });

  it("designates a unit, and ends a designation naming nothing else", async () => {
    const fetch = server({ "/api/tax-credit/units/u1": UNIT });
    render(<TaxCreditUnitCard unitId="u1" />);
    await screen.findByTestId("tax-credit-unit");

    let form = open("Set tax credit");
    fireEvent.change(within(form).getByLabelText("From"), { target: { value: "2026-11-01" } });
    fireEvent.change(within(form).getByLabelText("Household income up to"), { target: { value: "50" } });
    fireEvent.change(within(form).getByLabelText("Bedrooms"), { target: { value: "2" } });
    fireEvent.change(within(form).getByLabelText("Utility allowance a month"), { target: { value: " 75.00 " } });
    fireEvent.click(within(form).getByRole("button", { name: "Save tax credit" }));
    expect(await screen.findByText("A tax credit unit at 50% of median from 11/01/2026.")).toBeTruthy();

    form = open("Set tax credit");
    fireEvent.change(within(form).getByLabelText("From"), { target: { value: "2027-01-01" } });
    fireEvent.change(within(form).getByLabelText("Household income up to"), { target: { value: "" } });
    expect(within(form).queryByLabelText("Bedrooms")).toBeNull();
    fireEvent.click(within(form).getByRole("button", { name: "Save tax credit" }));
    expect(await screen.findByText("Not a tax credit unit from 01/01/2027.")).toBeTruthy();

    expect(posted(fetch)).toEqual([
      ["/api/tax-credit/units", {
        unit_id: "u1", starts_on: "2026-11-01", income_percent: 50, bedrooms: 2, utility_allowance: "75.00",
      }],
      ["/api/tax-credit/units", { unit_id: "u1", starts_on: "2027-01-01", income_percent: null }],
    ]);
  });
});

describe("TaxCreditLeaseCard", () => {
  it("shows nothing for a lease whose unit is not a tax credit unit", async () => {
    const fetch = server({ "/api/tax-credit/leases/l1": null, "/api/assistance/leases/l1": ASSISTANCE });
    const { container } = render(<TaxCreditLeaseCard leaseId="l1" />);
    await vi.waitFor(() => expect(fetch).toHaveBeenCalledTimes(2));
    expect(container.textContent).toBe("");
  });

  it("shows where the lease stands and records a certification", async () => {
    const fetch = server({ "/api/tax-credit/leases/l1": STANDING, "/api/assistance/leases/l1": ASSISTANCE });
    render(<TaxCreditLeaseCard leaseId="l1" />);

    expect((await screen.findByTestId("tax-credit-income")).textContent).toBe("over 140%: next available unit rule");
    expect(screen.getByTestId("tax-credit-certification").textContent).toBe("current");
    expect(screen.getByTestId("tax-credit-rent").textContent).toBe("over by 50.00");
    expect(screen.getByText("$1,014.00, the rent and the utility allowance; at most $964.00")).toBeTruthy();
    const certifications = await screen.findByRole("table", { name: "Income certifications" });
    expect(within(certifications).getAllByRole("row")[1]?.textContent).toBe("01/01/2026Move-in2$40,000.0001/01/2027");

    const form = open("Record certification");
    expect(within(form).getByText(/files nothing with the state agency/)).toBeTruthy();
    fireEvent.change(within(form).getByLabelText("Takes effect"), { target: { value: "2027-01-01" } });
    fireEvent.change(within(form).getByLabelText("People in the household"), { target: { value: "2" } });
    fireEvent.change(within(form).getByLabelText("Annual income"), { target: { value: "41000.00" } });
    fireEvent.click(within(form).getByRole("button", { name: "Record certification" }));
    expect(await screen.findByText("Annual certification from 01/01/2027 recorded.")).toBeTruthy();
    expect(posted(fetch)).toEqual([[
      "/api/assistance/leases/l1/certifications",
      { kind: "annual", effective_on: "2027-01-01", household_size: 2, annual_income: "41000.00", next_due_on: null },
    ]]);
  });

  it("says why it could not be read", async () => {
    server({});
    render(<TaxCreditLeaseCard leaseId="l1" />);
    expect((await screen.findByRole("alert")).textContent).toBe("not found");
  });
});
