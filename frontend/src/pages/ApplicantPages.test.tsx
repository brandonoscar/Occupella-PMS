import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { Me } from "../api";
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
    fireEvent.click(within(panel).getByRole("button", { name: "Reject" }));

    await screen.findByText("Rejected: Applicant A, Applicant B.");
    const sent = posted(fetch);
    expect(sent.map(([path]) => path)).toEqual(["/api/leasing/applications/a1/decide"]);
    expect(sent[0]?.[1]).toMatchObject({
      decision: "declined", conditions: null, screening_id: "sc1",
      adverse_action: { reasons: ["Income below 3x rent"] },
    });
  });

  it("approves without a notice when resting on no screening, or on conditions with one", async () => {
    const fetch = server();
    render(<ApplicantPage me={ME} id="a1" />);
    fireEvent.click(await screen.findByRole("button", { name: "Approve" }));
    const panel = screen.getByRole("dialog", { name: "Approve" });
    fireEvent.change(within(panel).getByLabelText("Resting on"), { target: { value: "" } });
    expect(within(panel).queryByLabelText("Adverse action reasons, one a line")).toBeNull();
    fireEvent.click(within(panel).getByRole("button", { name: "Approve" }));
    await screen.findByText("Approved: Applicant A, Applicant B.");

    fireEvent.click(screen.getByRole("button", { name: "Approve" }));
    const again = screen.getByRole("dialog", { name: "Approve" });
    fireEvent.change(within(again).getByLabelText("Decision"), { target: { value: "conditional" } });
    fireEvent.change(within(again).getByLabelText("Conditions"), { target: { value: "A co-signer" } });
    fireEvent.click(within(again).getByRole("button", { name: "Approve" }));
    await screen.findByText("Approved on conditions: Applicant A, Applicant B.");

    expect(posted(fetch).map(([, body]) => [body.decision, body.screening_id, body.conditions, body.adverse_action !== null]))
      .toEqual([["approved", null, null, false], ["conditional", "sc1", "A co-signer", true]]);
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
});
