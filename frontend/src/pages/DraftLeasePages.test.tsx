import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { Me } from "../api";
import { holdBack } from "../holdBack";
import type { Offer } from "../offers";
import { DraftLeasePage } from "./DraftLeasePage";
import { DraftLeasesPage, startedFrom } from "./DraftLeasesPage";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  window.history.replaceState(null, "", "/");
  localStorage.clear();
});

const ME: Me = {
  kind: "staff", email: "staff@example.com", name: "Staff 1", person_id: "s1", pmc_id: "p1", pmc_name: "Synthetic PMC",
};
const OFFER: Offer = {
  id: "o1", kind: "new lease", application_id: "a1", renews_lease_id: null, property_id: "pr1", property: "Property 1",
  unit_id: "u1", unit: "Unit 2", starts_on: "2027-01-01", ends_on: null, monthly_rent: "1500.00",
  security_deposit: "1500.00",
  recurring_charges: [{ kind: "fee", memo: "Pet rent", amount: "35.00" }, { kind: "credit", memo: "Autopay", amount: "10.00" }],
  document_title: "Lease", offered_at: "2026-10-12T14:00:00Z", sent: false, lease_id: null, status: "draft", void: null, closed: null,
  signers: [
    { position: 1, name: "Applicant A", email: "a@example.com", role: "tenant", signed_at: null, progress: "waiting" },
    { position: 2, name: "Applicant B", email: "b@example.com", role: "guarantor", signed_at: null, progress: "waiting" },
  ],
};
const APPROVED = {
  id: "a1", property_id: "pr1", property: "Property 1", unit_id: "u1", unit: "Unit 2", monthly_rent: "1500.00",
  move_in_on: "2027-01-01", monthly_income: "4500.00", submitted_at: "2026-10-10T00:00:00Z",
  applicants: [{ name: "Applicant A", email: "a@example.com" }], screenings: [], decision: null,
  status: "approved", withdrawal: null, lease_id: null,
};
const LEASE = {
  lease_id: "l1", property: "Property 1", unit: "Unit 3", starts_on: "2026-01-01", ends_on: "2026-12-31",
  monthly_rent: "1200.00", tenants: [{ name: "Tenant 1", email: "t1@example.com" }],
};
const LINKS = [
  { position: 1, name: "Applicant A", email: "a@example.com", url: "http://x/sign#one" },
  { position: 2, name: "Applicant B", email: "b@example.com", url: "http://x/sign#two" },
];

// The company's templates: a notice one and its own lease (migration 0121).
const TEMPLATES = {
  templates: [
    { id: "t1", version_id: "v1", name: "Late rent", body: "x", used_for: "notice", saved_at: "2026-10-20T15:00:00Z", saved_by: null },
    { id: "t2", version_id: "v2", name: "Residential lease", body: "y", used_for: "lease", saved_at: "2026-10-20T15:00:00Z", saved_by: null },
  ],
  fields: [],
  lease_fields: [],
};

function server(found: Offer = OFFER, refuse: Record<string, string> = {}) {
  const fetch = vi.fn(async (path: string, init?: RequestInit) => {
    if (init?.method === "POST") {
      if (path in refuse) return new Response(JSON.stringify({ detail: refuse[path] }), { status: 409 });
      if (path.endsWith("/send") || path.endsWith("/offer") || path.endsWith("/renewal")) {
        return new Response(JSON.stringify({ offer: { ...found, status: "sent", sent: true }, links: LINKS }), { status: 201 });
      }
      if (path.endsWith("/complete")) return new Response(JSON.stringify({ ...found, status: "leased", lease_id: "l9" }));
      if (path.endsWith("/links")) return new Response(JSON.stringify(LINKS));
      return new Response(JSON.stringify(found));
    }
    if (path === "/api/leasing/offers/o1") return new Response(JSON.stringify(found));
    if (path.startsWith("/api/leasing/offers?")) {
      const items = path.includes("show=void") ? [] : [found];
      return new Response(JSON.stringify({ items, total: items.length, page: 1, per_page: 50 }));
    }
    if (path.startsWith("/api/leasing/applications?")) {
      return new Response(JSON.stringify({ items: [APPROVED], total: 1, page: 1, per_page: 200 }));
    }
    if (path === "/api/leasing/leases") return new Response(JSON.stringify([LEASE]));
    if (path === "/api/property-groups") return new Response("[]");
    if (path === "/api/notices/templates") return new Response(JSON.stringify(TEMPLATES));
    return new Response(JSON.stringify({ detail: path }), { status: 404 });
  });
  vi.stubGlobal("fetch", fetch);
  return fetch;
}

const posted = (fetch: ReturnType<typeof server>) =>
  fetch.mock.calls.filter(([, init]) => init?.method === "POST").map(([path, init]) => [path, JSON.parse(String(init?.body))]);

const PDF = () => new File([new TextEncoder().encode("%PDF-1.4 a synthetic lease")], "lease.pdf", { type: "application/pdf" });

describe("startedFrom", () => {
  it("starts New draft lease on the application or lease the page was opened from", () => {
    expect(startedFrom("?application=a1")).toBe("application:a1");
    expect(startedFrom("?lease=l1")).toBe("lease:l1");
    expect(startedFrom("")).toBe("");
  });
});

describe("DraftLeasesPage", () => {
  it("lists draft leases with their status, and opens one's page", async () => {
    server();
    render(<DraftLeasesPage me={ME} />);

    const row = await screen.findByRole("row", { name: "Lease for Property 1, Unit 2" });
    expect(within(row).getAllByRole("cell").map((c) => c.textContent)).toEqual([
      "Property 1, Unit 2", "Applicant A, Applicant B", "01/01/2027", "$1,500.00", "Draft", "10/12/2026",
    ]);
    fireEvent.click(row);
    expect(window.location.pathname).toBe("/draft-leases/o1");
  });

  it("marks a renewal that can no longer go ahead", async () => {
    server({ ...OFFER, kind: "renewal", application_id: null, renews_lease_id: "l1", closed: "the lease it renews ended on 10/15/2026" });
    render(<DraftLeasesPage me={ME} />);

    const row = await screen.findByRole("row", { name: "Renewal for Property 1, Unit 2" });
    expect(within(row).getAllByRole("cell")[4]?.textContent).toBe("Draft Can't go ahead");
  });

  it("says when a tab has nothing", async () => {
    server();
    render(<DraftLeasesPage me={ME} />);
    await screen.findByRole("row", { name: /^Lease for/ });

    fireEvent.click(screen.getByRole("tab", { name: "Void" }));

    await screen.findByText("No draft lease was voided.");
  });

  it("shows the draft leases of the tab picked now, even when an earlier tab's answer comes back last", async () => {
    server();
    const release = holdBack((path) => path.startsWith("/api/leasing/offers?show=open"));
    render(<DraftLeasesPage me={ME} />);

    fireEvent.click(screen.getByRole("tab", { name: "Void" }));
    await screen.findByText("No draft lease was voided.");
    await release();

    expect(screen.getByText("No draft lease was voided.")).toBeTruthy();
    expect(screen.queryByRole("row", { name: "Lease for Property 1, Unit 2" })).toBeNull();
  });

  it("saves a draft with a deposit and monthly charges for an approved applicant", async () => {
    const fetch = server();
    window.history.replaceState(null, "", "/draft-leases?application=a1");
    render(<DraftLeasesPage me={ME} />);

    const panel = await screen.findByRole("dialog", { name: "New draft lease" });
    await within(panel).findByDisplayValue("Applicant A, a@example.com");
    fireEvent.change(within(panel).getByLabelText("Security deposit (optional)"), { target: { value: "1500.00" } });
    fireEvent.click(within(panel).getByRole("button", { name: "Add a monthly charge" }));
    const charge = within(panel).getByRole("group", { name: "Monthly charge 1" });
    fireEvent.change(within(charge).getByLabelText("What for"), { target: { value: "Pet rent" } });
    fireEvent.change(within(charge).getByLabelText("Amount"), { target: { value: "35.00" } });
    fireEvent.change(within(panel).getByLabelText("Lease document (PDF, your own)"), { target: { files: [PDF()] } });
    await within(panel).findByRole("button", { name: "Save draft" });
    await vi.waitFor(() => expect((within(panel).getByRole("button", { name: "Save draft" }) as HTMLButtonElement).disabled).toBe(false));
    fireEvent.click(within(panel).getByRole("button", { name: "Save draft" }));

    await vi.waitFor(() => expect(window.location.pathname).toBe("/draft-leases/o1"));
    expect(posted(fetch)).toEqual([[
      "/api/leasing/applications/a1/offer",
      {
        starts_on: "2027-01-01", ends_on: null, monthly_rent: "1500.00", security_deposit: "1500.00",
        recurring_charges: [{ kind: "fee", memo: "Pet rent", amount: "35.00" }], document_title: "Lease",
        document_base64: btoa("%PDF-1.4 a synthetic lease"), signers: [{ name: "Applicant A", email: "a@example.com" }],
        send: false,
      },
    ]]);
  });

  it("fills one of the company's lease templates instead of a PDF", async () => {
    const fetch = server();
    window.history.replaceState(null, "", "/draft-leases?application=a1");
    render(<DraftLeasesPage me={ME} />);

    const panel = await screen.findByRole("dialog", { name: "New draft lease" });
    await within(panel).findByDisplayValue("Applicant A, a@example.com");
    fireEvent.change(within(panel).getByLabelText("The lease"), { target: { value: "template" } });
    const pick = within(panel).getByLabelText("Lease template") as HTMLSelectElement;
    await vi.waitFor(() => expect(pick.value).toBe("t2"));
    expect([...pick.options].map((o) => o.textContent)).toEqual(["Residential lease"]);
    expect(within(panel).queryByLabelText("Lease document (PDF, your own)")).toBeNull();
    fireEvent.click(within(panel).getByRole("button", { name: "Save draft" }));

    await vi.waitFor(() => expect(window.location.pathname).toBe("/draft-leases/o1"));
    expect(posted(fetch)).toEqual([[
      "/api/leasing/applications/a1/offer",
      {
        starts_on: "2027-01-01", ends_on: null, monthly_rent: "1500.00", security_deposit: null,
        recurring_charges: [], template_id: "t2", signers: [{ name: "Applicant A", email: "a@example.com" }],
        send: false,
      },
    ]]);
  });

  it("says when the company has no lease template yet", async () => {
    const fetch = server();
    const base = fetch.getMockImplementation()!;
    fetch.mockImplementation(async (path: string, init?: RequestInit) =>
      path === "/api/notices/templates"
        ? new Response(JSON.stringify({ ...TEMPLATES, templates: [TEMPLATES.templates[0]] }))
        : base(path, init),
    );
    window.history.replaceState(null, "", "/draft-leases?application=a1");
    render(<DraftLeasesPage me={ME} />);
    const panel = await screen.findByRole("dialog", { name: "New draft lease" });
    await within(panel).findByDisplayValue("Applicant A, a@example.com");
    fireEvent.change(within(panel).getByLabelText("The lease"), { target: { value: "template" } });
    expect(within(panel).getByText(/No lease template yet/)).toBeTruthy();
    expect((within(panel).getByRole("button", { name: "Save draft" }) as HTMLButtonElement).disabled).toBe(true);
  });

  it("sends a renewal for signing at once, from the day after the lease ends", async () => {
    const fetch = server();
    window.history.replaceState(null, "", "/draft-leases?lease=l1");
    render(<DraftLeasesPage me={ME} />);

    const panel = await screen.findByRole("dialog", { name: "New draft lease" });
    await within(panel).findByDisplayValue("Tenant 1, t1@example.com");
    expect((within(panel).getByLabelText("Starts") as HTMLInputElement).value).toBe("2027-01-01");
    fireEvent.click(within(panel).getByRole("button", { name: "Add a monthly charge" }));
    fireEvent.click(within(panel).getByRole("button", { name: "Remove" }));
    fireEvent.change(within(panel).getByLabelText("Lease document (PDF, your own)"), { target: { files: [PDF()] } });
    // A guarantor signs too, without becoming a tenant.
    fireEvent.change(within(panel).getByLabelText(/Guarantors who sign too/), { target: { value: "Guarantor 1, g1@example.com" } });
    await vi.waitFor(() => expect((within(panel).getByRole("button", { name: "Send for signing" }) as HTMLButtonElement).disabled).toBe(false));
    fireEvent.click(within(panel).getByRole("button", { name: "Send for signing" }));

    await vi.waitFor(() => expect(posted(fetch)).toHaveLength(1));
    const sent = posted(fetch);
    expect(sent.map(([path]) => path)).toEqual(["/api/leasing/leases/l1/renewal"]);
    expect(sent[0]?.[1]).toMatchObject({ send: true, recurring_charges: [], security_deposit: null, document_title: "Lease renewal" });
    expect((sent[0]![1] as { signers: unknown }).signers).toEqual([
      { name: "Tenant 1", email: "t1@example.com" },
      { name: "Guarantor 1", email: "g1@example.com", role: "guarantor" },
    ]);
  });

  it("refuses a document that isn't a PDF and signers it can't read", async () => {
    const fetch = server();
    render(<DraftLeasesPage me={ME} />);
    fireEvent.click(await screen.findByRole("button", { name: "New draft lease" }));
    const panel = screen.getByRole("dialog", { name: "New draft lease" });
    await within(panel).findByRole("option", { name: "Applicant A for Property 1, Unit 2" });
    fireEvent.change(within(panel).getByLabelText("For"), { target: { value: "application:a1" } });

    fireEvent.change(within(panel).getByLabelText("Lease document (PDF, your own)"), {
      target: { files: [new File(["not a pdf"], "lease.txt")] },
    });
    await within(panel).findByText("the lease document is a PDF");
    fireEvent.change(within(panel).getByLabelText("Lease document (PDF, your own)"), { target: { files: [PDF()] } });
    fireEvent.change(within(panel).getByLabelText(/Who signs/), { target: { value: "No email here" } });
    await vi.waitFor(() => expect((within(panel).getByRole("button", { name: "Send for signing" }) as HTMLButtonElement).disabled).toBe(false));
    fireEvent.click(within(panel).getByRole("button", { name: "Send for signing" }));

    await within(panel).findByText('line 1: write each signer as "Name, email"');
    fireEvent.change(within(panel).getByLabelText(/Who signs/), { target: { value: "Applicant A, a@example.com" } });
    fireEvent.change(within(panel).getByLabelText(/Guarantors who sign too/), { target: { value: "Guarantor without email" } });
    fireEvent.click(within(panel).getByRole("button", { name: "Send for signing" }));
    await within(panel).findByText('Guarantors: line 1: write each signer as "Name, email"');
    expect(posted(fetch)).toEqual([]);
  });
});

describe("DraftLeasePage", () => {
  it("shows a draft's terms, deposit, monthly charges and signers, and sends it", async () => {
    const fetch = server();
    render(<DraftLeasePage me={ME} id="o1" />);

    expect((await screen.findByRole("heading", { level: 1 })).textContent).toBe("Lease for Property 1, Unit 2");
    expect(screen.getByTestId("status").textContent).toBe("Draft");
    const summary = screen.getByLabelText("Summary");
    expect(summary.textContent).toContain("Term01/01/2027 – Month to month");
    expect(summary.textContent).toContain("Security deposit$1,500.00");
    expect(within(summary).getByRole("link", { name: "Lease (PDF)" }).getAttribute("href")).toBe("/api/leasing/offers/o1/document");
    expect(within(screen.getByRole("table", { name: "Monthly charges" })).getAllByRole("row").slice(1).map((r) => r.textContent))
      .toEqual(["Pet rentFee$35.00", "AutopayCredit($10.00)"]);
    expect(screen.getByRole("table", { name: "Signers" }).textContent).toContain("Not sent");
    // A guarantor signs without becoming a tenant.
    const guarantor = within(screen.getByRole("table", { name: "Signers" })).getAllByRole("row")
      .find((r) => r.textContent?.startsWith("Applicant B"));
    expect(within(guarantor!).getAllByRole("cell").map((c) => c.textContent)).toEqual([
      "Applicant B Guarantor", "b@example.com", "", "Not sent",
    ]);
    expect(screen.queryByRole("button", { name: "Activate lease" })).toBeNull();

    fireEvent.click(screen.getByRole("button", { name: "Send" }));

    await screen.findByText("Sent to 2 signers.");
    expect(screen.getAllByTestId("signing-link").map((l) => l.textContent)).toEqual(["http://x/sign#one", "http://x/sign#two"]);
    expect(posted(fetch)).toEqual([["/api/leasing/offers/o1/send", {}]]);
  });

  it("shows how far each signer got, makes new links and activates the lease", async () => {
    const fetch = server({
      ...OFFER, status: "partially signed", sent: true,
      signers: [
        { position: 1, name: "Applicant A", email: "a@example.com", role: "tenant", signed_at: null, progress: "waiting" },
        { position: 2, name: "Applicant B", email: "b@example.com", role: "tenant", signed_at: "2026-10-13T10:00:00Z", progress: "signed" },
      ],
    });
    render(<DraftLeasePage me={ME} id="o1" />);

    expect(await screen.findByText("1 of 2 signed")).toBeTruthy();
    expect(screen.getAllByTestId("progress").map((b) => b.textContent)).toEqual(["Not started", "Signed"]);
    expect(within(screen.getByLabelText("Summary")).getByRole("link", { name: "Signing certificate (PDF)" })
      .getAttribute("href")).toBe("/api/leasing/offers/o1/certificate");
    expect(screen.queryByRole("button", { name: "Check signatures" })).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Resend" }));
    await screen.findByText("A new link for 2 signers.");
    expect(screen.getAllByTestId("signing-link").length).toBe(2);
    fireEvent.click(screen.getByRole("button", { name: "Activate lease" }));
    await screen.findByText("Property 1, Unit 2 has its lease from 01/01/2027.");

    expect(posted(fetch).map(([path]) => path)).toEqual(["/api/leasing/offers/o1/links", "/api/leasing/offers/o1/complete"]);
  });

  it("voids an offer from its ⋯ menu with why, and shows why the server refused", async () => {
    const fetch = server(OFFER, { "/api/leasing/offers/o1/void": "this offer is a lease already" });
    render(<DraftLeasePage me={ME} id="o1" />);
    fireEvent.click(await screen.findByRole("button", { name: "Actions for Property 1, Unit 2" }));
    fireEvent.click(screen.getByRole("menuitem", { name: "Void" }));

    const panel = screen.getByRole("dialog", { name: "Void" });
    fireEvent.change(within(panel).getByLabelText("Why (optional)"), { target: { value: " Terms changed " } });
    fireEvent.click(within(panel).getByRole("button", { name: "Void it" }));

    await within(panel).findByText("this offer is a lease already");
    expect(posted(fetch)).toEqual([["/api/leasing/offers/o1/void", { reason: "Terms changed" }]]);
  });

  it("shows a void offer's void and a leased one's lease, with no work left", async () => {
    server({ ...OFFER, status: "void", void: { reason: "Terms changed", recorded_at: "2026-10-14T09:00:00Z", recorded_by: "Staff 1" } });
    render(<DraftLeasePage me={ME} id="o1" />);

    expect((await screen.findByTestId("void")).textContent).toBe("10/14/2026 09:00 UTC, by Staff 1: Terms changed");
    expect(screen.queryByRole("button", { name: "Send" })).toBeNull();
    expect(screen.queryByRole("button", { name: /^Actions for/ })).toBeNull();
    cleanup();

    server({ ...OFFER, kind: "renewal", application_id: null, renews_lease_id: "l1", status: "leased", lease_id: "l9", sent: true });
    render(<DraftLeasePage me={ME} id="o1" />);
    expect((await screen.findByRole("link", { name: "Open the lease" })).getAttribute("href")).toBe("/leases/l9");
    expect(screen.getByRole("link", { name: "The lease" }).getAttribute("href")).toBe("/leases/l1");
    expect(screen.getByTestId("status").textContent).toBe("Lease active");
  });

  it("says a renewal whose lease ended can no longer go ahead, and offers only Void", async () => {
    const why = "the lease it renews ended on 10/15/2026";
    for (const status of ["draft", "partially signed", "signed"] as const) {
      const fetch = server({
        ...OFFER, kind: "renewal", application_id: null, renews_lease_id: "l1", status, sent: status !== "draft",
        closed: why,
      });
      render(<DraftLeasePage me={ME} id="o1" />);

      expect((await screen.findByTestId("closed")).textContent).toBe(
        "This renewal can no longer be sent, signed or made a lease: the lease it renews ended on 10/15/2026."
          + " Void it.",
      );
      expect(screen.getByText("Can't go ahead")).toBeTruthy();
      for (const gone of ["Send", "Resend", "Activate lease"]) {
        expect(screen.queryByRole("button", { name: gone })).toBeNull();
      }
      expect(screen.queryByRole("button", { name: /^Actions for/ })).toBeNull();
      fireEvent.click(screen.getByRole("button", { name: "Void" }));
      const panel = screen.getByRole("dialog", { name: "Void" });
      fireEvent.click(within(panel).getByRole("button", { name: "Void it" }));
      await vi.waitFor(() => expect(posted(fetch)).toEqual([["/api/leasing/offers/o1/void", { reason: null }]]));
      cleanup();
    }
  });

  it("says when the offer isn't found", async () => {
    server();
    render(<DraftLeasePage me={ME} id="zz" />);

    await screen.findByText("/api/leasing/offers/zz");
  });

  it("shows the draft lease opened now, even when an earlier draft lease's answer comes back last", async () => {
    const answers = server();
    vi.stubGlobal("fetch", vi.fn(async (path: string, init?: RequestInit) =>
      path === "/api/leasing/offers/o2"
        ? new Response(JSON.stringify({ ...OFFER, id: "o2", unit_id: "u5", unit: "Unit 5" }))
        : answers(path, init)));
    const release = holdBack((path) => path === "/api/leasing/offers/o1");
    const { rerender } = render(<DraftLeasePage me={ME} id="o1" />);
    rerender(<DraftLeasePage me={ME} id="o2" />);
    await screen.findByRole("heading", { level: 1, name: "Lease for Property 1, Unit 5" });
    await release();

    expect(screen.getByRole("heading", { level: 1 }).textContent).toBe("Lease for Property 1, Unit 5");
    expect(screen.queryByRole("heading", { level: 1, name: "Lease for Property 1, Unit 2" })).toBeNull();
  });
});
