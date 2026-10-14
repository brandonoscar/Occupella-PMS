import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { Me } from "../api";
import { day } from "../money";
import { StaffHome } from "./StaffHome";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

const ME: Me = {
  kind: "staff",
  email: "manager@example.com",
  name: "Manager 1",
  person_id: "s1",
  pmc_id: "p1",
  pmc_name: "Synthetic PMC",
  roles: ["admin", "approve", "disburse"],
};

describe("StaffHome", () => {
  it("downloads today's rent roll, trial balance and leases ending in the next 90 days", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response("[]", { status: 200 })));
    render(<StaffHome me={ME} />);
    const today = day(new Date());

    const href = (name: string) => screen.getByRole("link", { name }).getAttribute("href");
    expect(await screen.findByRole("heading", { name: "Synthetic PMC" })).toBeTruthy();
    expect(href("Rent roll (PDF)")).toBe(`/api/reports/trust_report_rent_roll?arg=${today}&format=pdf`);
    expect(href("Trial balance (Excel)")).toBe(
      `/api/reports/trust_report_trial_balance?arg=${today}&format=xlsx`,
    );
    expect(href("Leases ending in 90 days (Excel)")).toBe(
      `/api/reports/trust_report_lease_expirations?arg=${today}&format=xlsx`,
    );
    expect(href("Whole ledger (CSV, zip)")).toBe("/api/ledger.zip");
  });
});
