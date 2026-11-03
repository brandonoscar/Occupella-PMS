import { cleanup, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { Me } from "../api";
import type { Expiring } from "../workOrders";
import { RenewalsPage } from "./RenewalsPage";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

const ME: Me = {
  kind: "staff", email: "staff@example.com", name: "Staff 1", person_id: "s1", pmc_id: "p1",
  pmc_name: "Synthetic PMC",
};

const LEASES: Expiring[] = [
  { lease_id: "l1", property: "Property 1", unit: "Unit 1", tenants: "Tenant 1", ends_on: "2026-11-05",
    days_left: 20, bucket: "0-30", monthly_rent: "1500.00", renewal: "none" },
  { lease_id: null, property: "Property 2", unit: "House", tenants: "Tenant 2, Tenant 3",
    ends_on: "2026-12-30", days_left: 75, bucket: "61-90", monthly_rent: "2250.00", renewal: "sent" },
];

function server(answer: unknown, status = 200) {
  vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify(answer), { status })));
}

describe("RenewalsPage", () => {
  it("lists the leases ending soonest first, each with where its renewal stands", async () => {
    server(LEASES);
    render(<RenewalsPage me={ME} />);

    const first = await screen.findByRole("row", { name: "Property 1, Unit 1" });
    expect(within(first).getAllByRole("cell").map((c) => c.textContent)).toEqual([
      "Unit 1, Property 1", "Tenant 1", "11/05/2026 (in 20 days)", "$1,500.00", "No renewal",
    ]);
    expect(within(first).getByRole("link").getAttribute("href")).toBe("/leases/l1");
    // A lease the page can't name unambiguously reads without a link.
    expect(within(screen.getByRole("row", { name: "Property 2, House" })).queryByRole("link")).toBeNull();
    // A lease is renewed from its own page now, not from Draft leases.
    expect(screen.queryByRole("link", { name: "Draft a renewal" })).toBeNull();
    expect(screen.getByText(/Open a lease to renew it/)).toBeTruthy();
  });

  it("says when no lease ends soon, and when they can't be read", async () => {
    server([]);
    render(<RenewalsPage me={ME} />);
    expect((await screen.findByTestId("empty-state")).textContent).toContain("No lease ends in the next 90 days.");
    cleanup();

    server({ detail: "sign in first" }, 401);
    render(<RenewalsPage me={ME} />);
    expect((await screen.findByRole("alert")).textContent).toBe("sign in first");
  });
});
