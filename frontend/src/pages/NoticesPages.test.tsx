import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { Me } from "../api";
import type { Filled, MadeNotice, NoticeTemplate, Templates } from "../notices";
import { NoticeTemplatesPage } from "./NoticeTemplatesPage";
import { NoticesCard } from "./NoticesCard";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

const ME: Me = {
  kind: "staff", email: "staff@example.com", name: "Staff 1", person_id: "s1", pmc_id: "p1",
  pmc_name: "Synthetic PMC", roles: [],
};
const LATE: NoticeTemplate = {
  id: "t1", version_id: "v1", name: "Late rent", body: "Dear {{tenant_names}}, you owe {{balance_due}}.",
  used_for: "notice", saved_at: "2026-10-20T15:00:00Z", saved_by: "Staff 1",
};
const TEMPLATES: Templates = {
  templates: [LATE],
  fields: [
    { name: "tenant_names", what: "the lease's tenants" },
    { name: "balance_due", what: "what the lease owes now" },
  ],
  lease_fields: [{ name: "security_deposit", what: "the security deposit asked" }],
};
const LEASE: NoticeTemplate = {
  ...LATE, id: "t2", version_id: "v2", name: "Residential lease", body: "Deposit: {{security_deposit}}", used_for: "lease",
};
const FILLED: Filled = {
  title: "Late rent", letterhead: "Synthetic Homes LLC\n100 Example Way", body: "Dear Morgan Demo, you owe $1,395.00.",
};
const MADE: MadeNotice = {
  id: "n1", lease_id: "l1", title: "Late rent", dated_on: "2026-10-21", made_at: "2026-10-21T16:00:00Z", made_by: "Staff 1",
};

// GETs and POSTs answer `answers[path]` (POSTs with 201); `refuse[path]` answers 409.
function server(answers: Record<string, unknown>, refuse: Record<string, string> = {}) {
  const fetch = vi.fn(async (path: string, init?: RequestInit) => {
    if (path in refuse) return new Response(JSON.stringify({ detail: refuse[path] }), { status: 409 });
    if (!(path in answers)) return new Response(JSON.stringify({ detail: `no ${path}` }), { status: 404 });
    const body = answers[path];
    return body === null
      ? new Response(null, { status: 204 })
      : new Response(JSON.stringify(body), { status: init?.method === "POST" ? 201 : 200 });
  });
  vi.stubGlobal("fetch", fetch);
  return fetch;
}

const posted = (fetch: ReturnType<typeof server>) =>
  fetch.mock.calls
    .filter(([, init]) => init?.method === "POST")
    .map(([path, init]) => [path, JSON.parse(String(init?.body))]);

describe("NoticeTemplatesPage", () => {
  it("lists the templates and adds one, with the blanks the PMS fills beside the text", async () => {
    const fetch = server({
      "/api/notices/templates": TEMPLATES,
    });
    render(<NoticeTemplatesPage me={ME} />);
    const table = await screen.findByRole("table", { name: "Notice templates" });
    expect(within(table).getByRole("row", { name: "Late rent" }).textContent).toContain("10/20/2026 by Staff 1");

    fetch.mockImplementationOnce(async () => new Response(JSON.stringify({ ...LATE, id: "t2", name: "Entry" }), { status: 201 }));
    fireEvent.click(screen.getByRole("button", { name: "Add template" }));
    const panel = screen.getByRole("dialog", { name: "Add a notice template" });
    expect(within(panel).getByRole("region", { name: "Blanks the PMS fills" }).textContent).toContain(
      "{{tenant_names}} the lease's tenants",
    );
    fireEvent.change(within(panel).getByLabelText("Name"), { target: { value: "Entry" } });
    fireEvent.change(within(panel).getByLabelText("Text"), { target: { value: "We will enter {{unit}}." } });
    fireEvent.click(within(panel).getByRole("button", { name: "Save template" }));

    expect(await screen.findByText("Entry is saved.")).toBeTruthy();
    expect(posted(fetch)).toEqual([["/api/notices/templates", { name: "Entry", body: "We will enter {{unit}}.", used_for: "notice" }]]);
  });

  it("edits a template from its row and says why a save is refused", async () => {
    server({ "/api/notices/templates": TEMPLATES }, { "/api/notices/templates/t1": "a notice template is named Entry already" });
    render(<NoticeTemplatesPage me={ME} />);
    fireEvent.click(await screen.findByRole("row", { name: "Late rent" }));
    const panel = screen.getByRole("dialog", { name: "Edit Late rent" });
    expect((within(panel).getByLabelText("Text") as HTMLTextAreaElement).value).toBe(LATE.body);
    fireEvent.change(within(panel).getByLabelText("Name"), { target: { value: "Entry" } });
    fireEvent.click(within(panel).getByRole("button", { name: "Save template" }));
    expect((await within(panel).findByRole("alert")).textContent).toBe("a notice template is named Entry already");
  });

  it("removes a template once it is confirmed", async () => {
    const fetch = server({ "/api/notices/templates": TEMPLATES, "/api/notices/templates/t1/remove": null });
    render(<NoticeTemplatesPage me={ME} />);
    const row = await screen.findByRole("row", { name: "Late rent" });
    fireEvent.click(within(row).getByRole("button", { name: "Actions for Late rent" }));
    fireEvent.click(await screen.findByRole("menuitem", { name: "Remove" }));
    fireEvent.click(await screen.findByRole("button", { name: "Remove" }));

    expect(await screen.findByText("Late rent is removed; the notices made from it stay.")).toBeTruthy();
    expect(posted(fetch)).toEqual([["/api/notices/templates/t1/remove", {}]]);
  });

  it("keeps a lease template, with the blanks a draft lease's terms fill, its use fixed once made", async () => {
    const fetch = server({ "/api/notices/templates": { ...TEMPLATES, templates: [LATE, LEASE] } });
    render(<NoticeTemplatesPage me={ME} />);
    const table = await screen.findByRole("table", { name: "Notice templates" });
    expect(within(table).getByRole("row", { name: "Residential lease" }).textContent).toContain("A lease to sign");

    fetch.mockImplementationOnce(async () => new Response(JSON.stringify({ ...LEASE, id: "t3", name: "Short lease" }), { status: 201 }));
    fireEvent.click(screen.getByRole("button", { name: "Add template" }));
    const panel = screen.getByRole("dialog", { name: "Add a notice template" });
    fireEvent.change(within(panel).getByLabelText("Used for"), { target: { value: "lease" } });
    const blanks = within(panel).getByRole("region", { name: "Blanks the PMS fills" });
    expect(blanks.textContent).toContain("Blanks the PMS fills from the draft lease's terms:");
    expect(blanks.textContent).toContain("{{security_deposit}} the security deposit asked");
    fireEvent.change(within(panel).getByLabelText("Name"), { target: { value: "Short lease" } });
    fireEvent.change(within(panel).getByLabelText("Text"), { target: { value: "Deposit {{security_deposit}}" } });
    fireEvent.click(within(panel).getByRole("button", { name: "Save template" }));
    expect(await screen.findByText("Short lease is saved.")).toBeTruthy();
    expect(posted(fetch)).toEqual([["/api/notices/templates", { name: "Short lease", body: "Deposit {{security_deposit}}", used_for: "lease" }]]);

    // The page behind the panel is hidden from the accessibility tree until the panel has closed.
    fireEvent.click(await screen.findByRole("row", { name: "Residential lease" }));
    expect((screen.getByLabelText("Used for") as HTMLSelectElement).disabled).toBe(true);
  });

  it("says when there is no template yet", async () => {
    server({ "/api/notices/templates": { templates: [], fields: [], lease_fields: [] } });
    render(<NoticeTemplatesPage me={ME} />);
    expect(await screen.findByText("No notice template yet.")).toBeTruthy();
  });
});

describe("NoticesCard", () => {
  it("lists a lease's notices, each a PDF, and makes one after reading it filled in", async () => {
    const today = new Date().toISOString().slice(0, 10);
    // Another day than today, whatever today is: picking it asks for its own filled-in text.
    const later = new Date(Date.now() + 86_400_000).toISOString().slice(0, 10);
    const fetch = server({
      "/api/notices?lease_id=l1": [MADE],
      "/api/notices/templates": TEMPLATES,
      "/api/notices/preview": FILLED,
      "/api/notices": { ...MADE, id: "n2", dated_on: "2026-10-22" },
    });
    render(<NoticesCard leaseId="l1" />);
    const list = await screen.findByRole("list", { name: "Notices made" });
    expect(within(list).getByRole("link", { name: "Late rent (PDF)" }).getAttribute("href")).toBe("/api/notices/n1/pdf");
    expect(list.textContent).toContain("dated 10/21/2026, made 10/21/2026 by Staff 1");

    fireEvent.click(screen.getByRole("button", { name: "Write a notice" }));
    const filled = await screen.findByRole("article", { name: "The notice as it will read" });
    expect(filled.textContent).toContain("Dear Morgan Demo, you owe $1,395.00.");
    fireEvent.change(screen.getByLabelText("Dated"), { target: { value: later } });
    await screen.findByRole("article", { name: "The notice as it will read" });
    fireEvent.click(screen.getByRole("button", { name: "Make the notice" }));

    expect(await screen.findByText("Late rent, dated 10/22/2026, is made.")).toBeTruthy();
    expect(posted(fetch)).toEqual([
      ["/api/notices/preview", { lease_id: "l1", template_id: "t1", dated_on: today }],
      ["/api/notices/preview", { lease_id: "l1", template_id: "t1", dated_on: later }],
      ["/api/notices", { lease_id: "l1", template_id: "t1", dated_on: later }],
    ]);
  });

  it("offers only notice templates, not lease ones", async () => {
    server({
      "/api/notices?lease_id=l1": [],
      "/api/notices/templates": { ...TEMPLATES, templates: [LEASE] },
    });
    render(<NoticesCard leaseId="l1" />);
    fireEvent.click(await screen.findByRole("button", { name: "Write a notice" }));
    expect((await screen.findByRole("link", { name: "Settings, Notice templates" })).getAttribute("href")).toBe("/settings/notices");
  });

  it("sends staff to Settings when the company has no template, and says when there is no notice", async () => {
    server({ "/api/notices?lease_id=l1": [], "/api/notices/templates": { templates: [], fields: [], lease_fields: [] } });
    render(<NoticesCard leaseId="l1" />);
    expect(await screen.findByText("No notice made for this lease yet.")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Write a notice" }));
    expect((await screen.findByRole("link", { name: "Settings, Notice templates" })).getAttribute("href")).toBe("/settings/notices");
  });

  it("keeps only the filled-in notice for the day picked last, whichever answer comes back last", async () => {
    const answers: Record<string, (value: Response) => void> = {};
    vi.stubGlobal("fetch", vi.fn((path: string, init?: RequestInit) => {
      if (path === "/api/notices/preview") {
        const asked = JSON.parse(String(init?.body)).dated_on as string;
        return new Promise<Response>((resolve) => { answers[asked] = resolve; });
      }
      const body = path === "/api/notices/templates" ? TEMPLATES : [];
      return Promise.resolve(new Response(JSON.stringify(body), { status: 200 }));
    }));
    render(<NoticesCard leaseId="l1" />);
    fireEvent.click(await screen.findByRole("button", { name: "Write a notice" }));
    await vi.waitFor(() => expect(Object.keys(answers)).toHaveLength(1));
    fireEvent.change(screen.getByLabelText("Dated"), { target: { value: "2026-11-01" } });
    await vi.waitFor(() => expect(answers["2026-11-01"]).toBeTruthy());

    answers["2026-11-01"]!(new Response(JSON.stringify({ ...FILLED, body: "For November" }), { status: 200 }));
    await screen.findByText("For November");
    const first = Object.keys(answers).find((on) => on !== "2026-11-01")!;
    answers[first]!(new Response(JSON.stringify({ ...FILLED, body: "For today" }), { status: 200 }));
    await new Promise((settle) => setTimeout(settle, 20));
    expect(screen.queryByText("For today")).toBeNull();
    expect(screen.getByText("For November")).toBeTruthy();
  });
});
