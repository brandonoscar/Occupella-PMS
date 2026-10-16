import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { Me } from "../api";
import { day } from "../money";
import { ReportsPage } from "./ReportsPage";

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

describe("ReportsPage", () => {
  it("downloads today's reports and the whole ledger", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response("[]", { status: 200 })));
    render(<ReportsPage me={ME} />);
    const today = day(new Date());

    const href = (name: string) => screen.getByRole("link", { name }).getAttribute("href");
    expect(await screen.findByRole("heading", { name: "Reports" })).toBeTruthy();
    expect(href("Rent roll (PDF)")).toBe(`/api/reports/trust_report_rent_roll?arg=${today}&format=pdf`);
    expect(href("Trial balance (Excel)")).toBe(
      `/api/reports/trust_report_trial_balance?arg=${today}&format=xlsx`,
    );
    expect(href("Leases ending in 90 days (Excel)")).toBe(
      `/api/reports/trust_report_lease_expirations?arg=${today}&format=xlsx`,
    );
    expect(href("Balance sheet (PDF)")).toBe(`/api/reports/trust_report_balance_sheet?arg=${today}&format=pdf`);
    expect(screen.getAllByRole("link").map((link) => link.textContent)).toContain("Vendor directory (Excel)");
    expect(href("Whole ledger (CSV, zip)")).toBe("/api/ledger.zip");
  });

});
