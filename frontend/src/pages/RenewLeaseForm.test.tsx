import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { RenewalPlan } from "../offers";
import { whoSigns, yearFrom } from "../offers";
import { RenewLeaseForm } from "./RenewLeaseForm";
import { useRecord } from "./useRecord";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

const PLAN: RenewalPlan = {
  lease_id: "l1", property: "Property 1", unit: "Unit 1", starts_on: "2025-12-01", ends_on: "2026-11-30",
  monthly_rent: "1000.00", new_rent: "1000.00", new_starts_on: "2026-12-01",
  signers: [
    { tenant_id: "t1", name: "Tenant A", email: "a@example.com" },
    { tenant_id: "t2", name: "Tenant B", email: "b@example.com" },
  ],
  notice: null,
};

const WARNING = "California asks 90 days' written notice before a rent increase of 15.0% (California Civil Code §827)."
  + " This new rent starts in 37 days. To give that notice, start it on or after 01/23/2027.";

const TEMPLATES = {
  templates: [
    { id: "n1", version_id: "v1", name: "Late notice", body: "", used_for: "notice", saved_at: "", saved_by: null },
    { id: "k1", version_id: "v2", name: "Standard lease", body: "", used_for: "lease", saved_at: "", saved_by: null },
  ],
  fields: [],
  lease_fields: [],
};

// The API: the plan (as it stands, or for the rent and start asked, with the state's notice when
// the rent goes up), the company's templates, and the renewal sent.
function server(plan: RenewalPlan = PLAN, templates = TEMPLATES) {
  const fetch = vi.fn(async (path: string, init?: RequestInit) => {
    if (init?.method === "POST") {
      const offer = { property: "Property 1", unit: "Unit 1", signers: plan.signers };
      return new Response(JSON.stringify({ offer, links: [] }), { status: 201 });
    }
    if (path === "/api/notices/templates") return new Response(JSON.stringify(templates), { status: 200 });
    const asked = new URL(path, "http://test").searchParams;
    const rent = asked.get("monthly_rent");
    const raised = rent !== null && Number(rent) > Number(plan.monthly_rent);
    const found: RenewalPlan = {
      ...plan,
      new_rent: rent ?? plan.new_rent,
      new_starts_on: asked.get("starts_on") ?? plan.new_starts_on,
      notice: raised
        ? { state: "CA", days: 90, law: "California Civil Code §827", increase_percent: "15.0", earliest: "2027-01-23", warning: WARNING }
        : plan.notice,
    };
    return new Response(JSON.stringify(found), { status: 200 });
  });
  vi.stubGlobal("fetch", fetch);
  return fetch;
}

const posted = (fetch: ReturnType<typeof server>) =>
  fetch.mock.calls.filter(([, init]) => init?.method === "POST").map(([path, init]) => [path, JSON.parse(String(init?.body))]);

function Panel() {
  const { said, record } = useRecord(() => undefined);
  return (
    <>
      <p data-testid="said">{said}</p>
      <RenewLeaseForm leaseId="l1" record={record} />
    </>
  );
}

describe("yearFrom and whoSigns", () => {
  it("ends a renewal a year after it starts, the day before, and names who signs", () => {
    expect(yearFrom("2026-12-01")).toBe("2027-11-30");
    expect(yearFrom("2028-03-01")).toBe("2029-02-28");
    expect(whoSigns([{ name: "Tenant A", email: "a@example.com" }])).toBe("Tenant A (a@example.com)");
    expect(whoSigns(PLAN.signers.concat({ tenant_id: "t3", name: "Tenant C", email: "c@example.com" })))
      .toBe("Tenant A (a@example.com), Tenant B (b@example.com) and Tenant C (c@example.com)");
  });
});

describe("RenewLeaseForm (ADR 0010 phase 3, part L)", () => {
  it("starts the day after the lease ends at its rent, for a year, from the company's lease template", async () => {
    server();
    render(<Panel />);

    expect((await screen.findByLabelText<HTMLInputElement>("New rent a month")).value).toBe("1000.00");
    expect(screen.getByLabelText<HTMLInputElement>("Starts").value).toBe("2026-12-01");
    expect(screen.getByLabelText<HTMLInputElement>("Ends (empty for month to month)").value).toBe("2027-11-30");
    expect(screen.getByText("Unit 1, Property 1: $1,000.00 a month now, ending 11/30/2026.")).toBeTruthy();
    // Lease templates only, the first chosen; or the company's own PDF.
    const lease = screen.getByLabelText<HTMLSelectElement>("The lease");
    expect([...lease.options].map((o) => o.textContent)).toEqual(["Standard lease, filled in", "Your own PDF"]);
    expect(lease.value).toBe("k1");
    expect(within(await screen.findByRole("list", { name: "Who signs" })).getAllByRole("listitem").map((li) => li.textContent))
      .toEqual(["Tenant A, a@example.com", "Tenant B, b@example.com"]);
  });

  it("names who gets it before sending, then sends it to the tenants to sign", async () => {
    const fetch = server();
    render(<Panel />);
    await screen.findByLabelText("New rent a month");

    fireEvent.click(screen.getByRole("button", { name: "Send for signing" }));
    const ask = await screen.findByRole("alertdialog");
    expect(ask.textContent).toContain(
      "Send the renewal of Unit 1, Property 1, $1,000.00 a month from 12/01/2026, to sign to "
        + "Tenant A (a@example.com) and Tenant B (b@example.com)?",
    );
    fireEvent.click(within(ask).getByRole("button", { name: "Send" }));

    await vi.waitFor(() => expect(posted(fetch)).toHaveLength(1));
    expect(posted(fetch)).toEqual([["/api/leasing/leases/l1/renewal", {
      starts_on: "2026-12-01",
      ends_on: "2027-11-30",
      monthly_rent: "1000.00",
      recurring_charges: [],
      template_id: "k1",
      signers: [
        { name: "Tenant A", email: "a@example.com", role: "tenant" },
        { name: "Tenant B", email: "b@example.com", role: "tenant" },
      ],
      send: true,
    }]]);
    await vi.waitFor(() => expect(screen.getByTestId("said").textContent).toBe("Sent the renewal of Unit 1, Property 1. Sent to 2 signers."));
  });

  it("warns when the new rent starts inside the state's notice, and asks again before sending", async () => {
    const fetch = server();
    render(<Panel />);
    fireEvent.change(await screen.findByLabelText("New rent a month"), { target: { value: "1150.00" } });

    expect(await screen.findByText(WARNING)).toBeTruthy();
    expect(fetch.mock.calls.map(([path]) => path)).toContain("/api/leasing/renewals/l1/plan?monthly_rent=1150.00&starts_on=2026-12-01");
    fireEvent.click(screen.getByRole("button", { name: "Send for signing" }));
    const ask = await screen.findByRole("alertdialog");
    expect(ask.textContent).toContain(WARNING);
    fireEvent.click(within(ask).getByRole("button", { name: "Cancel" }));
    expect(posted(fetch)).toEqual([]);

    // Started on the first day that gives the notice, nothing is said but the notice it gives.
    server({ ...PLAN, notice: { state: "CA", days: 90, law: "California Civil Code §827", increase_percent: "15.0", earliest: "2027-01-23", warning: null } });
    cleanup();
    render(<Panel />);
    expect((await screen.findByTestId("renew-notice")).textContent)
      .toBe("90 days' written notice asked (California Civil Code §827); this start gives it.");
  });

  it("won't send while a tenant has no email on file, nor with no lease document", async () => {
    server({ ...PLAN, signers: [PLAN.signers[0]!, { tenant_id: "t2", name: "Tenant B", email: null }] }, { ...TEMPLATES, templates: [] });
    render(<Panel />);

    expect(await screen.findByText("Add an email for Tenant B on their page (Edit contact details) to send this.")).toBeTruthy();
    expect(screen.getByRole("list", { name: "Who signs" }).textContent).toContain("Tenant B: no email on file");
    expect(screen.getByRole<HTMLButtonElement>("button", { name: "Send for signing" }).disabled).toBe(true);
    // No lease template: the company's own PDF, which must be chosen first.
    expect(screen.getByLabelText<HTMLSelectElement>("The lease").value).toBe("pdf");
    expect(screen.getByLabelText("Lease document (PDF, your own)")).toBeTruthy();
  });

  it("says why the plan can't be read", async () => {
    vi.stubGlobal("fetch", vi.fn(async (path: string) => path === "/api/notices/templates"
      ? new Response(JSON.stringify(TEMPLATES), { status: 200 })
      : new Response(JSON.stringify({ detail: "no such lease" }), { status: 404 })));
    render(<Panel />);
    expect((await screen.findByRole("alert")).textContent).toBe("no such lease");
  });
});
