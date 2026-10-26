import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { Me } from "../api";
import { holdBack } from "../holdBack";
import type { Application } from "../leasing";
import { ApplicantPage } from "./ApplicantPage";
import { ApplicantsPage } from "./ApplicantsPage";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  window.history.replaceState(null, "", "/");
  localStorage.clear();
});

const ME: Me = {
  kind: "staff", email: "staff@example.com", name: "Staff 1", person_id: "s1", pmc_id: "p1", pmc_name: "Synthetic PMC",
};
const SCREENING = {
  id: "sc1", partner: "Fake screening", reference: "r1", recommendation: "conditional" as const,
  reasons: ["Income below 3x rent"], reported_at: "2026-10-12T15:00:00Z",
};
const APPLICATION: Application = {
  id: "a1", property_id: "pr1", property: "Property 1", unit_id: "u1", unit: "Unit 2", monthly_rent: "1500.00",
  move_in_on: "2026-11-01", monthly_income: "4000.00", submitted_at: "2026-10-12T14:00:00Z",
  applicants: [{ name: "Applicant A", email: "a@example.com" }, { name: "Applicant B", email: "b@example.com" }],
  screenings: [SCREENING], decision: null, status: "screening", withdrawal: null, lease_id: null,
};

function server(found: Application = APPLICATION, refuse: Record<string, string> = {}) {
  const fetch = vi.fn(async (path: string, init?: RequestInit) => {
    if (init?.method === "POST") {
      if (path in refuse) return new Response(JSON.stringify({ detail: refuse[path] }), { status: 409 });
      return new Response(JSON.stringify(found), { status: 201 });
    }
    if (path === "/api/leasing/applications/a1") return new Response(JSON.stringify(found));
    if (path.startsWith("/api/leasing/applications?")) {
      return new Response(JSON.stringify({ items: path.includes("show=withdrawn") ? [] : [found], total: 1, page: 1, per_page: 50 }));
    }
    if (path === "/api/property-groups") return new Response("[]");
    if (path === "/api/emails?about=application&about_id=a1") {
      return new Response(JSON.stringify([{
        id: "e1", kind: "adverse_action", to: "a@example.com", subject: "About your rental application: Cedar House, Unit 2",
        body: "Your application was approved on conditions: A co-signer", at: "2026-10-13T10:00:00Z", by: null,
        status: "sent", error: null,
      }]));
    }
    if (path.startsWith("/api/properties")) return new Response(JSON.stringify({ items: [], total: 0, page: 1, per_page: 200 }));
    return new Response(JSON.stringify({ detail: path }), { status: 404 });
  });
  vi.stubGlobal("fetch", fetch);
  return fetch;
}

const posted = (fetch: ReturnType<typeof server>) =>
  fetch.mock.calls.filter(([, init]) => init?.method === "POST").map(([path, init]) => [path, JSON.parse(String(init?.body))]);
const reads = (fetch: ReturnType<typeof server>) =>
  fetch.mock.calls.filter(([, init]) => init?.method !== "POST").map(([path]) => path);
// The plain line under a button (ADR 0010, rule 1): what describes it.
const says = (button: HTMLElement) => document.getElementById(button.getAttribute("aria-describedby") ?? "")?.textContent;

describe("ApplicantsPage", () => {
  it("lists each application with its status, screening and income to rent, and opens its page", async () => {
    const fetch = server();
    render(<ApplicantsPage me={ME} />);

    const row = await screen.findByRole("row", { name: "Application from Applicant A, Applicant B" });
    expect(within(row).getAllByRole("cell").map((c) => c.textContent)).toEqual([
      "Applicant A, Applicant B", "Property 1, Unit 2", "10/12/2026", "Screening", "Recommends conditions", "2.6x",
    ]);
    expect(reads(fetch)).toContain("/api/leasing/applications?show=open&page=1");
    fireEvent.click(row);
    expect(window.location.pathname).toBe("/applicants/a1");
  });

  it("shows each tab's applications and says when one has none", async () => {
    const fetch = server();
    render(<ApplicantsPage me={ME} />);
    await screen.findByRole("row", { name: /Application from/ });

    fireEvent.click(screen.getByRole("tab", { name: "Withdrawn" }));

    await screen.findByText("No application was withdrawn.");
    expect(reads(fetch)).toContain("/api/leasing/applications?show=withdrawn&page=1");
  });

  it("shows the applications of the tab picked now, even when an earlier tab's answer comes back last", async () => {
    server();
    const release = holdBack((path) => path.startsWith("/api/leasing/applications?show=open"));
    render(<ApplicantsPage me={ME} />);

    fireEvent.click(screen.getByRole("tab", { name: "Withdrawn" }));
    await screen.findByText("No application was withdrawn.");
    await release();

    expect(screen.getByText("No application was withdrawn.")).toBeTruthy();
    expect(screen.queryByRole("row", { name: "Application from Applicant A, Applicant B" })).toBeNull();
  });
});

describe("ApplicantPage", () => {
  it("shows the application, its applicants and screening, and screens it again", async () => {
    const fetch = server();
    render(<ApplicantPage me={ME} id="a1" />);

    expect((await screen.findByRole("heading", { level: 1 })).textContent).toBe("Applicant A, Applicant B");
    expect(screen.getByTestId("status").textContent).toBe("Screening");
    const summary = screen.getByLabelText("Summary");
    expect(summary.textContent).toContain("Income to rent2.6x");
    expect(within(summary).getByRole("link", { name: "Unit 2" }).getAttribute("href")).toBe("/units/u1");
    expect(within(screen.getByRole("list", { name: "Applicants" })).getAllByRole("listitem").map((l) => l.textContent))
      .toEqual(["Applicant A, a@example.com", "Applicant B, b@example.com"]);
    expect(screen.getByRole("list", { name: "Screenings" }).textContent).toContain("Recommends conditions: Income below 3x rent");

    expect(says(screen.getByRole("button", { name: "Screen" })))
      .toBe("Asks the screening partner and records its answer here. It decides nothing.");
    fireEvent.click(screen.getByRole("button", { name: "Screen" }));
    await screen.findByText("Screened Applicant A, Applicant B.");
    expect(posted(fetch)).toEqual([["/api/leasing/applications/a1/screen", {}]]);
  });

  it("rejects on the screening with the adverse action notice", async () => {
    const fetch = server();
    render(<ApplicantPage me={ME} id="a1" />);
    fireEvent.click(await screen.findByRole("button", { name: "Reject" }));

    const panel = screen.getByRole("dialog", { name: "Reject" });
    expect((within(panel).getByLabelText("Adverse action reasons, one a line") as HTMLTextAreaElement).value)
      .toBe("Income below 3x rent");
    // The screening recommends conditions: a rejection goes against it and says why (migration 0089).
    fireEvent.change(within(panel).getByLabelText("Why decide against the screening's recommendation"), {
      target: { value: " No co-signer came forward " },
    });
    expect(says(within(panel).getByRole("button", { name: "Reject" }))).toBe(
      "Rejects it; this is final. Each applicant gets the notice by email only if adverse action emails are on.",
    );
    fireEvent.click(within(panel).getByRole("button", { name: "Reject" }));

    await screen.findByText("Rejected: Applicant A, Applicant B.");
    const sent = posted(fetch);
    expect(sent.map(([path]) => path)).toEqual(["/api/leasing/applications/a1/decide"]);
    expect(sent[0]?.[1]).toMatchObject({
      decision: "declined", conditions: null, screening_id: "sc1",
      adverse_action: { reasons: ["Income below 3x rent"] }, override_reason: "No co-signer came forward",
    });
  });

  it("approves without a notice when resting on no screening, or on conditions with one", async () => {
    const fetch = server();
    render(<ApplicantPage me={ME} id="a1" />);
    fireEvent.click(await screen.findByRole("button", { name: "Approve" }));
    const panel = screen.getByRole("dialog", { name: "Approve" });
    fireEvent.change(within(panel).getByLabelText("Resting on"), { target: { value: "" } });
    expect(within(panel).queryByLabelText("Adverse action reasons, one a line")).toBeNull();
    expect(says(within(panel).getByRole("button", { name: "Approve" })))
      .toBe("Approves it; this is final. A draft lease comes next. Nothing is sent to the applicants.");
    // Approved outright against a screening that asks conditions: it says why.
    fireEvent.change(within(panel).getByLabelText("Why decide against the screening's recommendation"), {
      target: { value: "Pays a year ahead" },
    });
    fireEvent.click(within(panel).getByRole("button", { name: "Approve" }));
    await screen.findByText("Approved: Applicant A, Applicant B.");

    fireEvent.click(screen.getByRole("button", { name: "Approve" }));
    const again = screen.getByRole("dialog", { name: "Approve" });
    // A reason typed while approving outright is not sent once the decision follows the screening.
    fireEvent.change(within(again).getByLabelText("Why decide against the screening's recommendation"), {
      target: { value: "Typed, then changed" },
    });
    fireEvent.change(within(again).getByLabelText("Decision"), { target: { value: "conditional" } });
    fireEvent.change(within(again).getByLabelText("Conditions"), { target: { value: "A co-signer" } });
    // On conditions, as the screening recommends: no reason asked.
    expect(within(again).queryByLabelText("Why decide against the screening's recommendation")).toBeNull();
    expect(says(within(again).getByRole("button", { name: "Approve" }))).toBe(
      "Approves it on these conditions; this is final. Each applicant gets the notice by email only if adverse action emails are on.",
    );
    fireEvent.click(within(again).getByRole("button", { name: "Approve" }));
    await screen.findByText("Approved on conditions: Applicant A, Applicant B.");

    expect(posted(fetch).map(([, body]) => [body.decision, body.screening_id, body.conditions, body.adverse_action !== null, body.override_reason]))
      .toEqual([["approved", null, null, false, "Pays a year ahead"], ["conditional", "sc1", "A co-signer", true, null]]);
  });

  it("records a withdrawal from its ⋯ menu, and shows why the server refused one", async () => {
    const fetch = server(APPLICATION, { "/api/leasing/applications/a1/withdraw": "a rejected application is not withdrawn" });
    render(<ApplicantPage me={ME} id="a1" />);
    fireEvent.click(await screen.findByRole("button", { name: "Actions for Applicant A, Applicant B" }));
    fireEvent.click(screen.getByRole("menuitem", { name: "Record withdrawal" }));

    const panel = screen.getByRole("dialog", { name: "Record withdrawal" });
    fireEvent.change(within(panel).getByLabelText("Why (optional)"), { target: { value: "  Found another home " } });
    fireEvent.click(within(panel).getByRole("button", { name: "Record withdrawal" }));

    await within(panel).findByText("a rejected application is not withdrawn");
    expect(posted(fetch)).toEqual([["/api/leasing/applications/a1/withdraw", { reason: "Found another home" }]]);
  });

  it("makes an approved application a draft lease, and shows a decided one's notice", async () => {
    server({
      ...APPLICATION, status: "conditional",
      decision: {
        decision: "conditional", conditions: "A co-signer", screening_id: "sc1", decided_at: "2026-10-13T10:00:00Z",
        adverse_action: { reasons: ["Income below 3x rent"], notice_given_on: "2026-10-13" },
      },
    });
    render(<ApplicantPage me={ME} id="a1" />);

    const draft = await screen.findByRole("link", { name: "Create draft lease" });
    expect(draft.getAttribute("href")).toBe("/draft-leases?application=a1");
    expect(screen.queryByRole("button", { name: "Approve" })).toBeNull();
    expect(screen.getByTestId("decision").textContent).toContain("Approved on conditions 10/13/2026 10:00 UTC: A co-signer");
    expect(screen.getByRole("link", { name: "Notice (PDF)" }).getAttribute("href")).toBe("/api/leasing/applications/a1/notice");
    // The notice as it was emailed to each applicant, with whether it went.
    const emailed = await screen.findByRole("list", { name: "Email, Applicant A, Applicant B" });
    expect(emailed.textContent).toContain("Adverse action notice to a@example.com · Sent by the PMS");
  });

  it("marks a decision that went against the screening, with why", async () => {
    server({
      ...APPLICATION, status: "approved",
      decision: {
        decision: "approved", conditions: null, screening_id: null, decided_at: "2026-10-13T10:00:00Z",
        adverse_action: null, override_reason: "Pays a year ahead",
      },
    });
    render(<ApplicantPage me={ME} id="a1" />);

    const marked = await screen.findByTestId("override");
    expect(marked.textContent).toBe("Against the screening's recommendationPays a year ahead");
  });

  it("shows a withdrawn application's withdrawal and no work left on it", async () => {
    server({
      ...APPLICATION, status: "withdrawn",
      withdrawal: { reason: "Moving away", recorded_at: "2026-10-14T09:00:00Z", recorded_by: "Staff 1" },
    });
    render(<ApplicantPage me={ME} id="a1" />);

    expect((await screen.findByTestId("withdrawal")).textContent).toBe("10/14/2026 09:00 UTC, recorded by Staff 1: Moving away");
    expect(screen.getByTestId("status").textContent).toBe("Withdrawn");
    expect(screen.queryByRole("button", { name: "Screen" })).toBeNull();
    expect(screen.queryByRole("button", { name: /^Actions for/ })).toBeNull();
  });

  it("shows what each applicant gave and the household, and the tenant they became", async () => {
    server({
      ...APPLICATION,
      status: "approved",
      lease_id: "l9",
      applicants: [
        {
          id: "ap1", name: "Applicant A", email: "a@example.com", tenant_id: "t1",
          details: {
            phone: "555-555-0101",
            address: { line_1: "1 Sample Street", line_2: "Apt 2", city: "Sampleton", state: "ST", postal_code: "00001" },
            previous_landlord: { name: "Landlord 1", phone: "555-555-0150", from_on: "2023-01-01", to_on: "2025-12-31" },
            employer: "Employer 1",
            emergency_contact: { name: "Contact 1", phone: "555-555-0160", recorded_at: "2026-10-12T14:00:00Z" },
            income_proofs: ["d1"],
          },
        },
        {
          id: "ap2", name: "Applicant B", email: "b@example.com", tenant_id: null,
          details: { phone: null, address: null, previous_landlord: null, employer: null, emergency_contact: null, income_proofs: [] },
        },
      ],
      household: {
        occupants: ["Occupant 1"],
        pets: [
          { name: "Pet 1", kind: "Cat", breed: null, weight_lbs: "9.5", assistance_animal: false },
          { name: "Pet 2", kind: "Dog", breed: null, weight_lbs: null, assistance_animal: true },
        ],
        vehicles: [{ make: "Make 1", model: null, color: "Blue", plate: "SAMPLE1", plate_state: "ST" }],
      },
    });
    render(<ApplicantPage me={ME} id="a1" />);

    const gave = await screen.findByLabelText("What Applicant A gave");
    expect(within(gave).getByText("1 Sample Street, Apt 2, Sampleton, ST 00001")).toBeTruthy();
    const landlord = within(gave).getByText((_, node) => node?.tagName === "DD" && node.textContent === "Landlord 1, 555-555-0150, 01/01/2023 – 12/31/2025");
    // Phones are tel: links (ADR 0010, rule 6).
    expect(within(landlord).getByRole("link", { name: "555-555-0150" }).getAttribute("href")).toBe("tel:5555550150");
    expect(within(gave).getByText("Employer 1")).toBeTruthy();
    expect(within(gave).getByText((_, node) => node?.tagName === "DD" && node.textContent === "Contact 1, 555-555-0160")).toBeTruthy();
    expect(within(gave).getByRole("link", { name: "Proof of income" }).getAttribute("href")).toBe("/api/documents/d1/file");
    expect(screen.getByRole("link", { name: "Now a tenant" }).getAttribute("href")).toBe("/tenants/t1");
    expect(screen.getByText("No details came with this application.")).toBeTruthy();
    const household = screen.getByLabelText("Household");
    expect(within(household).getByText("Occupant 1")).toBeTruthy();
    expect(within(household).getByText("Pet 1, Cat, 9.5 lbs")).toBeTruthy();
    expect(within(household).getByText(/Pet 2, Dog \(assistance animal: no pet rent, fee or deposit\)/)).toBeTruthy();
    expect(within(household).getByText("Blue Make 1, SAMPLE1 (ST)")).toBeTruthy();
  });

  it("links a leased application to its lease", async () => {
    server({ ...APPLICATION, status: "approved", lease_id: "l9" });
    render(<ApplicantPage me={ME} id="a1" />);

    expect((await screen.findByRole("link", { name: "Open the lease" })).getAttribute("href")).toBe("/leases/l9");
    expect(screen.queryByRole("link", { name: "Create draft lease" })).toBeNull();
  });

  it("says when the application isn't found", async () => {
    server();
    render(<ApplicantPage me={ME} id="zz" />);

    await screen.findByText("/api/leasing/applications/zz");
  });

  it("shows the application opened now, even when an earlier application's answer comes back last", async () => {
    const answers = server();
    const other = { ...APPLICATION, id: "a2", applicants: [{ name: "Applicant C", email: "c@example.com" }] };
    vi.stubGlobal("fetch", vi.fn(async (path: string, init?: RequestInit) =>
      path === "/api/leasing/applications/a2" ? new Response(JSON.stringify(other)) : answers(path, init)));
    const release = holdBack((path) => path === "/api/leasing/applications/a1");
    const { rerender } = render(<ApplicantPage me={ME} id="a1" />);
    rerender(<ApplicantPage me={ME} id="a2" />);
    await screen.findByRole("heading", { level: 1, name: "Applicant C" });
    await release();

    expect(screen.getByRole("heading", { level: 1 }).textContent).toBe("Applicant C");
    expect(screen.queryByRole("heading", { level: 1, name: "Applicant A, Applicant B" })).toBeNull();
  });
});
