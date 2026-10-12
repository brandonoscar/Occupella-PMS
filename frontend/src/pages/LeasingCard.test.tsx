import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { LeasingCard } from "./LeasingCard";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

const SCREENING = {
  id: "s1",
  partner: "Fake Screening Partner",
  reference: "fake-1",
  recommendation: "decline",
  reasons: ["Household income is less than twice the rent"],
  reported_at: "2026-10-11T18:05:00Z",
};
const OPEN = {
  id: "a1",
  property: "Property 1",
  unit: "Unit 1",
  monthly_rent: "1500.00",
  move_in_on: "2026-12-01",
  monthly_income: "1000.00",
  submitted_at: "2026-10-11T18:00:00Z",
  applicants: [{ name: "Applicant A", email: "a@example.com" }],
  screenings: [SCREENING],
  decision: null,
};
const DECIDED = {
  ...OPEN,
  id: "a2",
  applicants: [{ name: "Applicant B", email: "b@example.com" }],
  decision: {
    decision: "declined",
    conditions: null,
    screening_id: "s1",
    decided_at: "2026-10-11T19:00:00Z",
    adverse_action: { reasons: ["Income"], notice_given_on: "2026-10-11" },
  },
};

function server() {
  const fetch = vi.fn(async (path: string, init?: RequestInit) => {
    if (init?.method === "POST") {
      const body = path === "/api/leasing/links" ? { id: "l9", url: "http://x/apply#new-token" } : {};
      return new Response(JSON.stringify(body), { status: 201 });
    }
    const answers: Record<string, unknown> = {
      "/api/leasing/units": [{ unit_id: "u1", property: "Property 1", unit: "Unit 1", leased_today: false }],
      "/api/leasing/links": [],
      "/api/leasing/applications?show=all": [OPEN, DECIDED],
    };
    return new Response(JSON.stringify(answers[path] ?? { detail: path }), {
      status: path in answers ? 200 : 404,
    });
  });
  vi.stubGlobal("fetch", fetch);
  return fetch;
}

const posts = (fetch: ReturnType<typeof server>) =>
  fetch.mock.calls
    .filter(([, init]) => init?.method === "POST")
    .map(([path, init]) => [path, JSON.parse(String(init?.body))]);

describe("LeasingCard", () => {
  it("opens a link for a unit and shows its URL once", async () => {
    const fetch = server();
    render(<LeasingCard />);

    await screen.findByRole("option", { name: "Property 1, Unit 1" });
    fireEvent.change(screen.getByLabelText("Unit"), { target: { value: "u1" } });
    fireEvent.change(screen.getByLabelText("Rent a month"), { target: { value: "1500" } });
    fireEvent.click(screen.getByRole("button", { name: "Open a link" }));

    expect((await screen.findByTestId("application-link")).textContent).toBe("http://x/apply#new-token");
    expect(posts(fetch)).toEqual([["/api/leasing/links", { unit_id: "u1", monthly_rent: "1500" }]]);
  });

  it("declines on the screening with its notice, prefilled from the screening's reasons", async () => {
    const fetch = server();
    const changed = vi.fn();
    render(<LeasingCard onChange={changed} />);

    const open = await screen.findByRole("listitem", { name: "Application from Applicant A" });
    expect(within(open).getByText(/0.6x the rent/)).toBeTruthy();
    fireEvent.change(within(open).getByLabelText("Decision"), { target: { value: "declined" } });
    expect((within(open).getByLabelText("Reasons, one a line") as HTMLTextAreaElement).value).toBe(
      "Household income is less than twice the rent",
    );
    fireEvent.click(within(open).getByRole("button", { name: "Record decision" }));

    await vi.waitFor(() => expect(posts(fetch)).toHaveLength(1));
    const [path, body] = posts(fetch)[0] as [string, Record<string, unknown>];
    expect(path).toBe("/api/leasing/applications/a1/decide");
    expect(body).toMatchObject({
      decision: "declined",
      conditions: null,
      screening_id: "s1",
      adverse_action: { reasons: ["Household income is less than twice the rent"] },
    });
    // The offers card hears of the decision.
    await vi.waitFor(() => expect(changed).toHaveBeenCalledTimes(1));
  });

  it("asks for no notice for an approval, and offers the notices already given", async () => {
    const fetch = server();
    render(<LeasingCard />);

    const open = await screen.findByRole("listitem", { name: "Application from Applicant A" });
    expect(within(open).queryByLabelText("Reasons, one a line")).toBeNull();
    fireEvent.click(within(open).getByRole("button", { name: "Screen" }));
    expect(screen.getByRole("link", { name: "Notice (PDF)" }).getAttribute("href")).toBe(
      "/api/leasing/applications/a2/notice",
    );

    await vi.waitFor(() =>
      expect(posts(fetch)).toEqual([["/api/leasing/applications/a1/screen", {}]]),
    );
  });
});
