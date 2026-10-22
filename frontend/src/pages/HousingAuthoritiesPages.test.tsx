import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { Me } from "../api";
import type { Authority, AuthorityPage } from "../assistance";
import { holdBack } from "../holdBack";
import { HousingAuthoritiesPage, HousingAuthorityPage } from "./HousingAuthoritiesPages";

beforeEach(() => window.history.replaceState(null, "", "/housing-authorities"));
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

const ME: Me = {
  kind: "staff", email: "staff@example.com", name: "Staff 1", person_id: "s1", pmc_id: "p1",
  pmc_name: "Synthetic PMC",
};

const ROWS: Authority[] = [
  { id: "h1", name: "Synthetic Housing Authority", leases: 2, owed: "1200.50" },
  { id: "h2", name: "Demo County Housing", leases: 0, owed: "0.00" },
];

const PAGE: AuthorityPage = {
  id: "h1",
  name: "Synthetic Housing Authority",
  owed: "1200.50",
  leases: [
    {
      lease_id: "l1", home: "Tenant 1, Birch Flats Unit A", monthly: "800.00", contract: "HAP-0001", owed: "800.00",
      open_charges: [{
        id: "c1", lease_id: "l1", authority_id: "h1", due_on: "2026-10-01", memo: "Rent due 2026-10-01", source: "rent",
        charged: "800.00", adjusted: "0.00", paid: "0.00", open: "800.00",
      }],
      hold_since: null, hold_reason: null,
    },
    {
      lease_id: "l2", home: "Tenant 2, Birch Flats Unit B", monthly: "400.50", contract: null, owed: "400.50",
      open_charges: [], hold_since: null, hold_reason: null,
    },
    {
      lease_id: "l3", home: "Tenant 3, Birch Flats Unit C", monthly: null, contract: null, owed: "0.00", open_charges: [],
      hold_since: null, hold_reason: null,
    },
  ],
  late_fee: null,
  payments: [],
};

function server(answers: Record<string, unknown> = {}, refuse = "") {
  const fetch = vi.fn(async (path: string, init?: RequestInit) => {
    if (init?.method === "POST") {
      if (refuse) return new Response(JSON.stringify({ detail: refuse }), { status: 422 });
      return new Response(JSON.stringify({ id: "h9", transfers: ["t1"], paid: "1200.50" }), { status: 201 });
    }
    if (path in answers) return new Response(JSON.stringify(answers[path]), { status: 200 });
    return new Response(JSON.stringify({ detail: "no such housing authority" }), { status: 404 });
  });
  vi.stubGlobal("fetch", fetch);
  return fetch;
}

const posted = (fetch: ReturnType<typeof server>) =>
  fetch.mock.calls.filter(([, init]) => init?.method === "POST").map(([path, init]) => [path, JSON.parse(String(init?.body))]);

describe("HousingAuthoritiesPage", () => {
  it("lists each authority with the leases it assists and what it owes; a row opens its page", async () => {
    server({ "/api/assistance/authorities": ROWS });
    render(<HousingAuthoritiesPage me={ME} />);

    const row = await screen.findByRole("row", { name: "Synthetic Housing Authority" });
    expect(within(row).getAllByRole("cell").map((c) => c.textContent)).toEqual([
      "Synthetic Housing Authority", "2", "$1,200.50",
    ]);
    fireEvent.click(row);
    expect(window.location.pathname).toBe("/housing-authorities/h1");
  });

  it("says when there are none, and adds one in a side panel", async () => {
    const fetch = server({ "/api/assistance/authorities": [] });
    render(<HousingAuthoritiesPage me={ME} />);
    expect(await screen.findByText("No housing authorities yet")).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: "Add housing authority" }));
    const form = screen.getByRole("dialog", { name: "Add housing authority" });
    fireEvent.change(within(form).getByLabelText("Housing authority's name"), { target: { value: "Demo County Housing" } });
    fireEvent.click(within(form).getByRole("button", { name: "Add housing authority" }));

    await vi.waitFor(() => expect(window.location.pathname).toBe("/housing-authorities/h9"));
    expect(posted(fetch)).toEqual([["/api/assistance/authorities", { name: "Demo County Housing" }]]);
  });

  it("shows a refused name in the panel, and a list that didn't load", async () => {
    server({ "/api/assistance/authorities": [] }, "a housing authority by that name is on file already");
    render(<HousingAuthoritiesPage me={ME} />);
    await screen.findByText("No housing authorities yet");
    fireEvent.click(screen.getByRole("button", { name: "Add housing authority" }));
    const form = screen.getByRole("dialog", { name: "Add housing authority" });
    fireEvent.change(within(form).getByLabelText("Housing authority's name"), { target: { value: "Demo County Housing" } });
    fireEvent.click(within(form).getByRole("button", { name: "Add housing authority" }));
    expect(await within(form).findByText("a housing authority by that name is on file already")).toBeTruthy();

    cleanup();
    server();
    render(<HousingAuthoritiesPage me={ME} />);
    expect(await screen.findByText("no such housing authority")).toBeTruthy();
  });
});

describe("HousingAuthorityPage", () => {
  it("lists each lease it assists or owes on, linked to the lease", async () => {
    server({ "/api/assistance/authorities/h1": PAGE });
    render(<HousingAuthorityPage me={ME} id="h1" />);

    await screen.findByRole("heading", { level: 1, name: "Synthetic Housing Authority" });
    expect(screen.getByTestId("authority-owed").textContent).toBe("Owes $1,200.50 in all.");
    expect(within(screen.getByRole("row", { name: "Tenant 1, Birch Flats Unit A" })).getAllByRole("cell").map((c) => c.textContent))
      .toEqual(["Tenant 1, Birch Flats Unit A", "HAP-0001", "$800.00", "$800.00", "10/01/2026"]);
    expect(within(screen.getByRole("row", { name: "Tenant 3, Birch Flats Unit C" })).getAllByRole("cell").map((c) => c.textContent))
      .toEqual(["Tenant 3, Birch Flats Unit C", "", "None today", "$0.00", ""]);
    fireEvent.click(screen.getByRole("link", { name: "Tenant 1, Birch Flats Unit A" }));
    expect(window.location.pathname).toBe("/leases/l1");
  });

  it("shows the housing authority opened now, even when an earlier authority's answer comes back last", async () => {
    server({
      "/api/assistance/authorities/h1": PAGE,
      "/api/assistance/authorities/h2": { ...PAGE, id: "h2", name: "Demo County Housing", owed: "0.00", leases: [] },
    });
    const release = holdBack((path) => path === "/api/assistance/authorities/h1");
    const { rerender } = render(<HousingAuthorityPage me={ME} id="h1" />);
    rerender(<HousingAuthorityPage me={ME} id="h2" />);
    await screen.findByRole("heading", { level: 1, name: "Demo County Housing" });
    await release();

    expect(screen.getByRole("heading", { level: 1, name: "Demo County Housing" })).toBeTruthy();
    expect(screen.queryByRole("heading", { level: 1, name: "Synthetic Housing Authority" })).toBeNull();
  });

  it("records one payment for several leases, each no more than it owes", async () => {
    const fetch = server({ "/api/assistance/authorities/h1": PAGE });
    render(<HousingAuthorityPage me={ME} id="h1" />);
    await screen.findByRole("heading", { level: 1, name: "Synthetic Housing Authority" });

    fireEvent.click(screen.getByRole("button", { name: "Record payment" }));
    const form = screen.getByRole("dialog", { name: "Record payment" });
    // Only the leases it owes on take a part.
    expect(within(form).queryByLabelText(/Tenant 3/)).toBeNull();
    const submit = within(form).getByRole("button", { name: "Record payment" }) as HTMLButtonElement;
    expect(submit.disabled).toBe(true);
    fireEvent.click(within(form).getByRole("button", { name: "Fill in what it owes" }));
    expect(within(form).getByTestId("authority-pay-total").textContent).toBe("Total $1,200.50");
    fireEvent.change(within(form).getByLabelText("Tenant 2, Birch Flats Unit B (owes $400.50)"), { target: { value: "" } });
    fireEvent.change(within(form).getByLabelText("Check or reference"), { target: { value: " HAP 1001 " } });
    fireEvent.change(within(form).getByLabelText("Method"), { target: { value: "check" } });
    expect(within(form).getByTestId("authority-pay-total").textContent).toBe("Total $800.00");
    fireEvent.click(submit);

    expect(await screen.findByText("$800.00 from Synthetic Housing Authority recorded.")).toBeTruthy();
    expect(posted(fetch)).toEqual([["/api/assistance/payments", {
      request_id: expect.stringMatching(/^[0-9a-f-]{36}$/), housing_authority_id: "h1", received_on: expect.any(String),
      method: "check", reference: "HAP 1001", lines: [{ lease_id: "l1", amount: "800.00" }],
    }]]);
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("shows a refused payment in the panel, and says when nothing is owed", async () => {
    server({ "/api/assistance/authorities/h1": PAGE }, "800.01 is more than Synthetic Housing Authority owes");
    render(<HousingAuthorityPage me={ME} id="h1" />);
    await screen.findByRole("heading", { level: 1, name: "Synthetic Housing Authority" });
    fireEvent.click(screen.getByRole("button", { name: "Record payment" }));
    const form = screen.getByRole("dialog", { name: "Record payment" });
    fireEvent.change(within(form).getByLabelText("Tenant 1, Birch Flats Unit A (owes $800.00)"), { target: { value: "800.01" } });
    fireEvent.click(within(form).getByRole("button", { name: "Record payment" }));
    expect(await within(form).findByText("800.01 is more than Synthetic Housing Authority owes")).toBeTruthy();

    cleanup();
    const paid = { ...PAGE, owed: "0.00", leases: [PAGE.leases[2]] };
    server({ "/api/assistance/authorities/h1": paid });
    render(<HousingAuthorityPage me={ME} id="h1" />);
    await screen.findByRole("heading", { level: 1, name: "Synthetic Housing Authority" });
    fireEvent.click(screen.getByRole("button", { name: "Record payment" }));
    expect(screen.getByText("The authority owes nothing on its leases.")).toBeTruthy();
  });

  it("sets the authority's late payment fee in a side panel and says it (q034)", async () => {
    const fetch = server({ "/api/assistance/authorities/h1": PAGE });
    render(<HousingAuthorityPage me={ME} id="h1" />);
    await screen.findByRole("heading", { level: 1, name: "Synthetic Housing Authority" });
    expect(screen.getByTestId("authority-late-fee").textContent).toBe("No late payment fee.");

    fireEvent.click(screen.getByRole("button", { name: "Set late fee" }));
    const form = screen.getByRole("dialog", { name: "Set late fee" });
    fireEvent.change(within(form).getByLabelText("In force from"), { target: { value: "2026-10-01" } });
    fireEvent.change(within(form).getByLabelText("Grace days"), { target: { value: "10" } });
    fireEvent.change(within(form).getByLabelText("Late payment fee"), { target: { value: "25.00" } });
    fireEvent.click(within(form).getByRole("button", { name: "Set late fee" }));

    expect(await screen.findByText("Late payment fee of $25.00 from 10/01/2026.")).toBeTruthy();
    expect(posted(fetch)).toEqual([["/api/assistance/authorities/h1/late-fees", {
      starts_on: "2026-10-01", grace_days: 10, flat_fee: "25.00",
    }]]);

    cleanup();
    server({ "/api/assistance/authorities/h1": {
      ...PAGE, late_fee: { starts_on: "2026-10-01", grace_days: 1, flat_fee: "25.00" },
    } });
    render(<HousingAuthorityPage me={ME} id="h1" />);
    await screen.findByRole("heading", { level: 1, name: "Synthetic Housing Authority" });
    expect(screen.getByTestId("authority-late-fee").textContent).toBe(
      "A late payment fee of $25.00 when its part of a rent is still open 1 day after it was due (from 10/01/2026).",
    );
  });

  it("lists its payments; It bounced is confirmed with the amount, and a bounced one says so", async () => {
    const payments = [
      { transfer_id: "t1", received_on: "2026-10-03", amount: "800.00", reference: "HAP 7", method: "EFT" as const, bounced_on: null },
      { transfer_id: "t0", received_on: "2026-09-03", amount: "400.50", reference: null, method: null, bounced_on: "2026-09-09" },
    ];
    const fetch = server({ "/api/assistance/authorities/h1": { ...PAGE, payments } });
    render(<HousingAuthorityPage me={ME} id="h1" />);
    const table = await screen.findByRole("table", { name: "Payments" });
    expect(within(table).getByRole("row", { name: "09/03/2026 payment of $400.50" }).textContent).toContain("Bounced 09/09/2026");

    fireEvent.click(within(table).getByRole("button", { name: "Actions for 10/03/2026 payment of $800.00" }));
    fireEvent.click(await screen.findByRole("menuitem", { name: "It bounced" }));
    const asking = screen.getByRole("alertdialog");
    expect(asking.textContent).toContain("Mark the $800.00 payment bounced today?");
    fireEvent.click(within(asking).getByRole("button", { name: "It bounced" }));

    expect(await screen.findByText("The $800.00 payment bounced; the authority owes it again.")).toBeTruthy();
    expect(posted(fetch)).toEqual([["/api/assistance/payments/t1/bounce", { on: expect.stringMatching(/^\d{4}-\d{2}-\d{2}$/) }]]);
  });

  it("marks a lease on a payment hold and takes a payment past it only when asked", async () => {
    const held = { ...PAGE, leases: [{ ...PAGE.leases[0]!, hold_since: "2026-10-02", hold_reason: "Eviction filed" }] };
    const fetch = server({ "/api/assistance/authorities/h1": held });
    render(<HousingAuthorityPage me={ME} id="h1" />);
    const row = await screen.findByRole("row", { name: "Tenant 1, Birch Flats Unit A" });
    expect(row.textContent).toContain("On a payment hold since 10/02/2026: Eviction filed");

    fireEvent.click(screen.getByRole("button", { name: "Record payment" }));
    const form = screen.getByRole("dialog", { name: "Record payment" });
    fireEvent.click(within(form).getByRole("button", { name: "Fill in what it owes" }));
    fireEvent.click(within(form).getByLabelText("Accept this payment past the payment hold"));
    fireEvent.click(within(form).getByRole("button", { name: "Record payment" }));

    await screen.findByText("$800.00 from Synthetic Housing Authority recorded.");
    expect(posted(fetch)[0]![1]).toMatchObject({ past_hold: true });
  });

  it("says when it has no leases yet, or didn't load", async () => {
    server({ "/api/assistance/authorities/h1": { ...PAGE, owed: "0.00", leases: [] } });
    render(<HousingAuthorityPage me={ME} id="h1" />);
    expect(await screen.findByText("No leases yet")).toBeTruthy();
    expect((screen.getByRole("button", { name: "Record payment" }) as HTMLButtonElement).disabled).toBe(true);

    cleanup();
    server();
    render(<HousingAuthorityPage me={ME} id="h2" />);
    expect(await screen.findByText("no such housing authority")).toBeTruthy();
  });
});
