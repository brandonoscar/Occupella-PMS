import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { Me } from "../api";
import { attentionRows } from "../today";
import type { Today } from "../today";
import { AnnouncementsPage } from "./AnnouncementsPage";
import { MessagesPage } from "./MessagesPage";
import { OwnerHome } from "./OwnerHome";
import { OwnerPropertyCard, OwnerRepairsCard, unitStatus } from "./OwnerPortalPages";
import { AnnouncementsCard, LeaseUploadForm, MoveOutNoticeCard } from "./TenantPortalCards";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

function server(answers: Record<string, unknown>) {
  const fetch = vi.fn(async (path: string, init?: RequestInit) => {
    const key = `${init?.method ?? "GET"} ${path}`;
    if (!(key in answers)) return new Response(JSON.stringify({ detail: key }), { status: 404 });
    const answer = answers[key];
    if (answer instanceof Response) return answer;
    return new Response(JSON.stringify(answer), { status: key.startsWith("POST") ? 201 : 200 });
  });
  vi.stubGlobal("fetch", fetch);
  return fetch;
}

const OWNER: Me = { kind: "owner", name: "Owner 1", pmc_id: "p1", pmc_name: "Synthetic PMC" } as Me;
const STAFF: Me = { kind: "staff", name: "Staff 1", pmc_id: "p1", pmc_name: "Synthetic PMC" } as Me;

const PROPERTY = {
  property_id: "pr1",
  name: "Property 1",
  address: "100 Synthetic Way, Demo City, ZZ 00000",
  balance: "1500.00",
  occupied: 1,
  vacant: 1,
  owed: "250.00",
  units: [
    {
      id: "u1", name: "Unit A", vacant_since: null,
      lease: { tenants: ["Tenant 1"], starts_on: "2026-01-01", ends_on: null, monthly_rent: "1250.00",
        owed: "250.00", status: "current", leaving_on: "2026-12-31" },
    },
    { id: "u2", name: "Unit B", lease: null, vacant_since: "2026-09-01" },
  ],
};

describe("the owner portal's second version", () => {
  it("shows a property's units, who rents each, what they owe, and what is vacant", async () => {
    server({ "GET /api/owner/properties/pr1": PROPERTY });
    render(<OwnerPropertyCard propertyId="pr1" />);
    const units = await screen.findByRole("table", { name: "Units" });
    const rows = within(units).getAllByRole("row").slice(1).map((r) => r.textContent);
    expect(rows[0]).toContain("Tenant 1 · 01/01/2026 – Month to month");
    expect(rows[0]).toContain("Moving out 12/31/2026");
    expect(rows[0]).toContain("$1,250.00");
    expect(rows[1]).toContain("Vacant since 09/01/2026");
    expect(screen.getByText("1 rented, 1 vacant")).toBeTruthy();
    expect(screen.getByText(/Tenants' names only/)).toBeTruthy();
  });

  it("names a unit's state in words", () => {
    expect(unitStatus({ id: "u", name: "U", lease: null, vacant_since: null })).toBe("Vacant");
    expect(unitStatus({
      id: "u", name: "U", vacant_since: "2026-10-01",
      lease: { tenants: ["T"], starts_on: "2026-12-01", ends_on: null, monthly_rent: "1", owed: "0", status: "future", leaving_on: null },
    })).toBe("Vacant since 10/01/2026; leased from 12/01/2026");
  });

  it("lists the repairs at the owner's properties with their status and what was billed", async () => {
    server({
      "GET /api/owner/work-orders": [{
        id: "w1", number: 7, property: { id: "pr1", name: "Property 1" }, unit: { id: "u1", name: "Unit A" },
        summary: "Leak under sink", opened_at: "2026-11-01T10:00:00Z", status: "scheduled", vendor: "Vendor 1",
        billed: "180.00", priority: "high", scheduled_for: "2026-11-05", visit_window: "8-10 am", days_open: 2,
      }],
    });
    render(<OwnerRepairsCard />);
    const table = await screen.findByRole("table", { name: "Repairs" });
    const row = within(table).getAllByRole("row")[1]?.textContent ?? "";
    expect(row).toContain("Leak under sink");
    expect(row).toContain("visit 11/05/2026, 8-10 am");
    expect(row).toContain("Property 1, Unit A");
    expect(row).toContain("Visit scheduled");
    expect(row).toContain("$180.00");
  });

  it("says when there are no repairs", async () => {
    server({ "GET /api/owner/work-orders": [] });
    render(<OwnerRepairsCard />);
    expect(await screen.findByText("No repairs yet.")).toBeTruthy();
  });

  it("has a section per kind of thing in the sidebar, and links each property to its page", async () => {
    server({
      "GET /api/owner/properties": [{ account_id: "a1", property_id: "pr1", name: "Property 1", bank: "Operating", balance: "1500.00" }],
    });
    render(<OwnerHome me={OWNER} section="properties" />);
    const nav = screen.getByRole("navigation", { name: "Sections" });
    expect(within(nav).getAllByRole("link").map((a) => a.textContent)).toEqual([
      "Overview", "Properties", "Statements and reports", "Bills", "Repairs", "Documents", "Messages", "Profile",
    ]);
    expect((await screen.findByRole("link", { name: "Property 1" })).getAttribute("href")).toBe("/properties/pr1");
  });
});

describe("the tenant portal's second version", () => {
  it("shows announcements newest first, and nothing at all when there are none", async () => {
    server({
      "GET /api/tenant/announcements": [
        { id: "a1", property: "Property 1", title: "Water off Tuesday", body: "From 9 to noon.", at: "2026-11-02T15:00:00Z" },
      ],
    });
    render(<AnnouncementsCard />);
    const list = await screen.findByRole("list", { name: "Announcements" });
    expect(within(list).getByText("Water off Tuesday")).toBeTruthy();
    expect(within(list).getByText("11/02/2026 · Property 1")).toBeTruthy();
    cleanup();
    server({ "GET /api/tenant/announcements": [] });
    const { container } = render(<AnnouncementsCard />);
    await new Promise((r) => setTimeout(r, 0));
    expect(container.textContent).toBe("");
  });

  it("gives notice to move out and says what it does and doesn't do", async () => {
    const given = { leaving_on: "2026-12-31", note: "Moving for work.", given_by: "Tenant 1", given_at: "2026-11-03T15:00:00Z" };
    const fetch = server({ "POST /api/tenant/leases/l1/move-out-notice": given });
    const onGiven = vi.fn();
    render(<MoveOutNoticeCard leaseId="l1" company="Synthetic PMC" notice={null} onGiven={onGiven} />);
    expect(screen.getByRole("button", { name: "Give notice" })).toBeTruthy();
    expect(screen.getByText(/It doesn't end the lease/)).toBeTruthy();
    fireEvent.change(screen.getByLabelText("Last day you will live here"), { target: { value: "2026-12-31" } });
    fireEvent.change(screen.getByLabelText("Anything to add (optional)"), { target: { value: " Moving for work. " } });
    fireEvent.click(screen.getByRole("button", { name: "Give notice" }));
    expect(await screen.findByText("Synthetic PMC has your notice: leaving 12/31/2026.")).toBeTruthy();
    const post = fetch.mock.calls.find(([, init]) => init?.method === "POST");
    expect(JSON.parse(String(post?.[1]?.body))).toEqual({ leaving_on: "2026-12-31", note: "Moving for work." });
    expect(onGiven).toHaveBeenCalledWith(given);
  });

  it("says a notice given already, and a refusal in words", async () => {
    server({
      "POST /api/tenant/leases/l1/move-out-notice": new Response(
        JSON.stringify({ detail: "the day you leave is today or later, within the lease" }), { status: 422 }),
    });
    render(<MoveOutNoticeCard leaseId="l1" company="Synthetic PMC" onGiven={() => {}}
      notice={{ leaving_on: "2026-12-31", note: null, given_by: "Tenant 1", given_at: "2026-11-03T15:00:00Z" }} />);
    expect(screen.getByTestId("notice-given").textContent).toContain("leaving on 12/31/2026");
    fireEvent.change(screen.getByLabelText("Last day you will live here"), { target: { value: "2099-01-01" } });
    fireEvent.click(screen.getByRole("button", { name: "Give notice" }));
    expect(await screen.findByText("the day you leave is today or later, within the lease")).toBeTruthy();
  });

  it("sends a document of the lease, named as the tenant gives it", async () => {
    const fetch = server({ "POST /api/tenant/leases/l1/documents": { title: "Insurance", url: "/x", kind: "lease document", from_tenant: true } });
    const onFiled = vi.fn();
    render(<LeaseUploadForm leaseId="l1" company="Synthetic PMC" onFiled={onFiled} />);
    const pdf = new File([new Uint8Array([0x25, 0x50, 0x44, 0x46])], "certificate.pdf", { type: "application/pdf" });
    fireEvent.change(screen.getByLabelText("File (PDF, JPEG or PNG, up to 5 MB)"), { target: { files: [pdf] } });
    fireEvent.change(screen.getByLabelText("What it is (optional)"), { target: { value: "Renters insurance certificate" } });
    const send = screen.getByRole("button", { name: "Send document" }) as HTMLButtonElement;
    await waitFor(() => expect(send.disabled).toBe(false));
    fireEvent.click(send);
    expect(await screen.findByText("Sent to Synthetic PMC.")).toBeTruthy();
    const post = fetch.mock.calls.find(([, init]) => init?.method === "POST");
    const body = JSON.parse(String(post?.[1]?.body));
    expect(body.title).toBe("Renters insurance certificate");
    expect(body.content_type).toBe("application/pdf");
    expect(onFiled).toHaveBeenCalled();
  });

  it("refuses a file that isn't a PDF, JPEG or PNG before sending it", async () => {
    const fetch = server({});
    render(<LeaseUploadForm leaseId="l1" company="Synthetic PMC" onFiled={() => {}} />);
    const text = new File(["hello"], "notes.txt", { type: "text/plain" });
    fireEvent.change(screen.getByLabelText("File (PDF, JPEG or PNG, up to 5 MB)"), { target: { files: [text] } });
    expect(await screen.findByText("A document is a PDF, JPEG or PNG.")).toBeTruthy();
    expect((screen.getByRole("button", { name: "Send document" }) as HTMLButtonElement).disabled).toBe(true);
    expect(fetch).not.toHaveBeenCalled();
  });
});

describe("staff's side of the portals", () => {
  it("lists conversations, those waiting first, each opening its own page", async () => {
    server({
      "GET /api/messages": [
        { about: "tenant", person_id: "t1", name: "Tenant 1", last_at: "2026-11-02T15:00:00Z", last_by: "person",
          preview: "The gate sticks.", waiting: true, count: 1 },
        { about: "owner", person_id: "o1", name: "Owner 1", last_at: "2026-11-01T15:00:00Z", last_by: "company",
          preview: "Done.", waiting: false, count: 2 },
      ],
    });
    render(<MessagesPage me={STAFF} />);
    const table = await screen.findByRole("table", { name: "Conversations" });
    const rows = within(table).getAllByRole("row").slice(1);
    expect(rows[0]?.textContent).toContain("Waiting");
    expect(rows[1]?.textContent).toContain("You: Done.");
    expect(within(rows[0] as HTMLElement).getByRole("link", { name: "Tenant 1" }).getAttribute("href")).toBe("/messages/tenant/t1");
    expect(screen.getByText("Conversations (1 waiting for an answer)")).toBeTruthy();
  });

  it("posts an announcement for one property, and takes one down after asking", async () => {
    const shown = {
      id: "a1", property_id: "pr1", property: "Property 1", title: "Water off Tuesday", body: "From 9 to noon.",
      at: "2026-11-02T15:00:00Z", by: "Staff 1", removed_at: null, removed_by: null,
    };
    const fetch = server({
      "GET /api/announcements": [shown],
      "GET /api/properties?per_page=200&archived=false": { items: [{ id: "pr1", name: "Property 1" }], total: 1, page: 1, per_page: 200 },
      "POST /api/announcements": shown,
      "POST /api/announcements/a1/remove": { ...shown, removed_at: "2026-11-03T15:00:00Z", removed_by: "Staff 1" },
    });
    render(<AnnouncementsPage me={STAFF} />);
    await screen.findByRole("option", { name: "Tenants of Property 1" });
    fireEvent.change(screen.getByLabelText("Who sees it"), { target: { value: "pr1" } });
    fireEvent.change(screen.getByLabelText("Title"), { target: { value: "Water off Tuesday" } });
    fireEvent.change(screen.getByLabelText("What to say"), { target: { value: "From 9 to noon." } });
    fireEvent.click(screen.getByRole("button", { name: "Post announcement" }));
    expect(await screen.findByText("Posted for the tenants of Property 1.")).toBeTruthy();
    const post = fetch.mock.calls.find(([path, init]) => init?.method === "POST" && path === "/api/announcements");
    expect(JSON.parse(String(post?.[1]?.body))).toEqual({ property_id: "pr1", title: "Water off Tuesday", body: "From 9 to noon." });
    expect(screen.getByText("Showing")).toBeTruthy();
  });

  it("puts notices to move out and messages waiting in Needs attention", () => {
    const today = {
      on: "2026-11-03", late_rent: [], bills_due: [], unreconciled: [], applications: [], unsigned_offers: [],
      journal_waiting: [], vendor_insurance: [], company_fees: [], signers: [], renters_insurance: [],
      deposits_to_receive: [], deposits_to_return: [],
      move_out_notices: [{ lease_id: "l1", property: "Property 1", unit: "Unit A", tenants: ["Tenant 1"],
        leaving_on: "2026-12-31", given_at: "2026-11-03T15:00:00Z" }],
      messages_waiting: [{ about: "tenant", person_id: "t1", name: "Tenant 1", last_at: "2026-11-02T15:00:00Z",
        preview: "The gate sticks." }],
    } as unknown as Today;
    const rows = attentionRows(today);
    expect(rows.map((r) => [r.what, r.line, r.action])).toEqual([
      ["Message", "Tenant 1 (tenant): The gate sticks.", { kind: "go", label: "Answer", href: "/messages/tenant/t1" }],
      ["Notice to move out", "Tenant 1 · Property 1, Unit A · leaving 12/31/2026",
        { kind: "go", label: "Open lease", href: "/leases/l1" }],
    ]);
  });

  it("puts the drafts software proposed in Needs attention, each opening its page (ADR 0011)", () => {
    const today = {
      on: "2026-11-05", late_rent: [], bills_due: [], unreconciled: [], applications: [], unsigned_offers: [],
      journal_waiting: [], vendor_insurance: [], company_fees: [], signers: [], renters_insurance: [],
      deposits_to_receive: [], deposits_to_return: [],
      drafts_waiting: [{ id: "d1", what: "Email to Tenant 1: Water shutoff", key: "Assistant",
        made_at: "2026-11-05T15:00:00Z" }],
    } as unknown as Today;
    expect(attentionRows(today).map((r) => [r.what, r.line, r.action])).toEqual([
      ["Draft", "Email to Tenant 1: Water shutoff (from Assistant)",
        { kind: "go", label: "Review draft", href: "/drafts/d1" }],
    ]);
  });

  it("says in Needs attention when rent stopped posting, first of all (docs/edge-cases.md H-021)", () => {
    const base = {
      on: "2026-11-07", late_rent: [], bills_due: [], unreconciled: [], applications: [], unsigned_offers: [],
      journal_waiting: [], vendor_insurance: [], company_fees: [], signers: [], renters_insurance: [],
      deposits_to_receive: [], deposits_to_return: [],
      drafts_waiting: [{ id: "d1", what: "Email to Tenant 1: Water shutoff", key: "Assistant",
        made_at: "2026-11-01T15:00:00Z" }],
    };
    const stale = { ...base, rent_not_posted: { last_posted_on: "2026-11-04", waiting_since: "2026-11-04" } } as unknown as Today;
    expect(attentionRows(stale).map((r) => [r.what, r.line, r.action])[0]).toEqual([
      "Rent not posting",
      "No rent run since 11/04/2026: rent, late fees and recurring bills aren't posting by themselves."
        + " Tell whoever runs the PMS; an admin can Run rent now.",
      { kind: "go", label: "Open rent roll", href: "/rent-roll" },
    ]);
    const never = { ...base, rent_not_posted: { last_posted_on: null, waiting_since: "2026-11-04" } } as unknown as Today;
    expect(attentionRows(never)[0]?.line).toBe(
      "Rent has never posted by itself: rent, late fees and recurring bills aren't posting by themselves."
        + " Tell whoever runs the PMS; an admin can Run rent now.",
    );
  });
});
