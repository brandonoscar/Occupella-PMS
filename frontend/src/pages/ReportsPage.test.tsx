import { cleanup, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import type { Me } from "../api";
import { DataExportPage } from "./DataExportPage";
import { ReportsPage } from "./ReportsPage";

afterEach(cleanup);

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
  it("lists every report in its group, each opening its own page", () => {
    render(<ReportsPage me={ME} />);

    expect(within(screen.getByRole("list", { name: "Rentals" })).getAllByRole("link").map((l) => l.textContent)).toEqual([
      "Rent roll", "Delinquency", "Vacancy", "Lease expirations", "Tenant directory", "Renters insurance",
    ]);
    expect(within(screen.getByRole("list", { name: "Vendors" })).getAllByRole("link").map((l) => l.textContent)).toEqual([
      "Vendor directory", "Vendor insurance", "1099 summary", "Unpaid bills",
    ]);
    expect(within(screen.getByRole("list", { name: "Maintenance" })).getByRole("link", { name: "Work orders" }).getAttribute("href"))
      .toBe("/reports/work_orders");
    // The whole ledger lives under Settings, Data export.
    expect(screen.queryByRole("link", { name: "Whole ledger (CSV, zip)" })).toBeNull();
  });
});

describe("DataExportPage", () => {
  it("downloads the whole ledger", () => {
    render(<DataExportPage me={ME} />);
    expect(screen.getByRole("link", { name: "Whole ledger (CSV, zip)" }).getAttribute("href")).toBe("/api/ledger.zip");
  });
});
