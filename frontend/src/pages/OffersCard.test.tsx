import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { Offer } from "../offers";
import { OffersCard } from "./OffersCard";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

const STATUS: Record<string, string> = { approved: "approved", declined: "rejected", conditional: "conditional" };
const application = (id: string, name: string, decision: string | null) => ({
  id,
  property_id: "p1",
  property: "Property 1",
  unit_id: "u1",
  unit: "Unit 1",
  monthly_rent: "1500.00",
  move_in_on: "2027-01-01",
  monthly_income: "4500.00",
  submitted_at: "2026-10-11T18:00:00Z",
  applicants: [{ name, email: `${id}@example.com` }],
  screenings: [],
  decision: decision && {
    decision,
    conditions: null,
    screening_id: null,
    decided_at: "2026-10-11T19:00:00Z",
    adverse_action: null,
  },
  status: decision ? STATUS[decision] : "new",
  withdrawal: null,
  lease_id: null,
});

const LEASE = {
  lease_id: "l1",
  property: "Property 1",
  unit: "Unit 2",
  starts_on: "2026-07-01",
  ends_on: "2027-06-30",
  monthly_rent: "1200.00",
  tenants: [
    { name: "Tenant 1", email: "t1@example.com" },
    { name: "Tenant 2", email: null },
  ],
};

const OPEN: Offer = {
  id: "o1",
  kind: "renewal",
  application_id: null,
  renews_lease_id: "l1",
  property: "Property 1",
  unit: "Unit 2",
  starts_on: "2027-07-01",
  ends_on: null,
  monthly_rent: "1250.00",
  document_title: "Lease renewal",
  offered_at: "2026-10-11T18:00:00Z",
  sent: true,
  signers: [
    { position: 1, name: "Tenant 1", email: "t1@example.com", signed_at: "2026-10-11T19:00:00Z" },
    { position: 2, name: "Tenant 2", email: "t2@example.com", signed_at: null },
  ],
  lease_id: null,
};
// An approved application whose offer is a lease already.
const DONE: Offer = { ...OPEN, id: "o2", kind: "new lease", application_id: "a3", lease_id: "l9" };
const LINKS = [
  { position: 1, name: "Tenant 1", email: "t1@example.com", url: "http://x/esign#one" },
  { position: 2, name: "Tenant 2", email: "t2@example.com", url: "http://x/esign#two" },
];

// The API, with the offers it has so far; a refused completion answers 409.
function server({ refuseCompletion = false } = {}) {
  const offers = [OPEN, DONE];
  const fetch = vi.fn(async (path: string, init?: RequestInit) => {
    const answer = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status });
    if (init?.method === "POST") {
      if (path.endsWith("/offer") || path.endsWith("/renewal")) {
        const made = { ...OPEN, id: "o3", unit: "Unit 1" };
        offers.unshift(made);
        return answer({ offer: made, links: LINKS }, 201);
      }
      if (path === "/api/leasing/offers/o1/complete") {
        return refuseCompletion
          ? answer({ detail: "this offer is not signed by everyone yet" }, 409)
          : answer({ ...OPEN, lease_id: "l10" });
      }
      return answer(OPEN);
    }
    const answers: Record<string, unknown> = {
      "/api/leasing/applications?show=open&per_page=200": {
        items: [
          application("a1", "Applicant A", "approved"),
          application("a3", "Applicant C", "conditional"),
          application("a4", "Applicant D", null),
        ],
        total: 3,
        page: 1,
        per_page: 200,
      },
      "/api/leasing/leases": [LEASE],
      "/api/leasing/offers?show=all": offers,
      "/api/leasing/offers/o1/links": LINKS,
    };
    return path in answers ? answer(answers[path]) : answer({ detail: path }, 404);
  });
  vi.stubGlobal("fetch", fetch);
  return fetch;
}

const posts = (fetch: ReturnType<typeof server>) =>
  fetch.mock.calls
    .filter(([, init]) => init?.method === "POST")
    .map(([path, init]) => [path, JSON.parse(String(init?.body))]);

// The document is read: the button to send takes it.
const ready = () =>
  vi.waitFor(() =>
    expect((screen.getByRole("button", { name: "Send for signing" }) as HTMLButtonElement).disabled).toBe(
      false,
    ),
  );

const pdf = (text = "%PDF-1.4 a synthetic lease") =>
  new File([text], "lease.pdf", { type: "application/pdf" });

describe("OffersCard", () => {
  it("offers only approved applications not yet a lease, and the leases to renew", async () => {
    server();
    render(<OffersCard />);

    const offer = await screen.findByLabelText("Offer");
    await within(offer).findByRole("option", { name: "Applicant A for Property 1, Unit 1" });
    const names = within(offer).getAllByRole("option").map((o) => o.textContent);
    expect(names).toEqual([
      "Choose an application or a lease",
      "Applicant A for Property 1, Unit 1",
      "Property 1, Unit 2: Tenant 1, Tenant 2",
    ]);
  });

  it("starts from the approved application its page sent it, once that is loaded", async () => {
    server();
    render(<OffersCard application="a1" />);

    await screen.findByDisplayValue("Applicant A, a1@example.com");
    expect((screen.getByLabelText("Offer") as HTMLSelectElement).value).toBe("application:a1");
    expect((screen.getByLabelText("Rent a month") as HTMLInputElement).value).toBe("1500.00");
  });

  it("starts from nothing when the application named isn't one to offer", async () => {
    server();
    render(<OffersCard application="a4" />);

    await screen.findByRole("option", { name: "Applicant A for Property 1, Unit 1" });
    expect((screen.getByLabelText("Offer") as HTMLSelectElement).value).toBe("");
  });

  it("sends a lease for an application with its applicants and the PDF, and shows the links", async () => {
    const fetch = server();
    render(<OffersCard />);

    await screen.findByRole("option", { name: "Applicant A for Property 1, Unit 1" });
    fireEvent.change(screen.getByLabelText("Offer"), { target: { value: "application:a1" } });
    expect((screen.getByLabelText("Starts") as HTMLInputElement).value).toBe("2027-01-01");
    expect((screen.getByLabelText("Rent a month") as HTMLInputElement).value).toBe("1500.00");
    expect((screen.getByLabelText(/Who signs/) as HTMLTextAreaElement).value).toBe(
      "Applicant A, a1@example.com",
    );
    fireEvent.change(screen.getByLabelText("Ends (empty for month to month)"), {
      target: { value: "2027-12-31" },
    });
    fireEvent.change(screen.getByLabelText("Lease document (PDF, your own)"), {
      target: { files: [pdf()] },
    });
    await ready();
    fireEvent.click(screen.getByRole("button", { name: "Send for signing" }));

    const made = await screen.findByRole("listitem", { name: "Offer for Unit 1" });
    expect(within(made).getAllByTestId("signing-link").map((l) => l.textContent)).toEqual([
      "http://x/esign#one",
      "http://x/esign#two",
    ]);
    expect(posts(fetch)).toEqual([
      [
        "/api/leasing/applications/a1/offer",
        {
          starts_on: "2027-01-01",
          ends_on: "2027-12-31",
          monthly_rent: "1500.00",
          document_title: "Lease",
          document_base64: btoa("%PDF-1.4 a synthetic lease"),
          signers: [{ name: "Applicant A", email: "a1@example.com" }],
        },
      ],
    ]);
    // The form starts over.
    expect((screen.getByLabelText("Offer") as HTMLSelectElement).value).toBe("");
  });

  it("renews a lease from the day after it ends, once every signer has an email", async () => {
    const fetch = server();
    render(<OffersCard />);

    await screen.findByRole("option", { name: "Property 1, Unit 2: Tenant 1, Tenant 2" });
    fireEvent.change(screen.getByLabelText("Offer"), { target: { value: "lease:l1" } });
    expect((screen.getByLabelText("Starts") as HTMLInputElement).value).toBe("2027-07-01");
    expect((screen.getByLabelText("Document title") as HTMLInputElement).value).toBe("Lease renewal");
    fireEvent.change(screen.getByLabelText("Lease document (PDF, your own)"), {
      target: { files: [pdf()] },
    });
    const signers = screen.getByLabelText(/Who signs/);
    await ready();
    fireEvent.click(screen.getByRole("button", { name: "Send for signing" }));

    expect((await screen.findByRole("alert")).textContent).toBe(
      'line 2: write each signer as "Name, email"',
    );
    fireEvent.change(signers, {
      target: { value: "Tenant 1, t1@example.com\nTenant 2, t2@example.com" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Send for signing" }));

    await vi.waitFor(() => expect(posts(fetch)).toHaveLength(1));
    const [path, body] = posts(fetch)[0] as [string, Record<string, unknown>];
    expect(path).toBe("/api/leasing/leases/l1/renewal");
    expect(body).toMatchObject({
      starts_on: "2027-07-01",
      ends_on: null,
      monthly_rent: "1200.00",
      signers: [
        { name: "Tenant 1", email: "t1@example.com" },
        { name: "Tenant 2", email: "t2@example.com" },
      ],
    });
  });

  it("takes only a PDF of at most 5 MB, and needs one", async () => {
    const fetch = server();
    render(<OffersCard />);

    await screen.findByRole("option", { name: "Applicant A for Property 1, Unit 1" });
    fireEvent.change(screen.getByLabelText("Offer"), { target: { value: "application:a1" } });
    const document = screen.getByLabelText("Lease document (PDF, your own)");
    fireEvent.change(document, { target: { files: [new File(["hello"], "lease.txt")] } });
    expect((await screen.findByRole("alert")).textContent).toBe("the lease document is a PDF");

    const large = new Uint8Array(5_000_001);
    large.set(new TextEncoder().encode("%PDF-"));
    fireEvent.change(document, { target: { files: [new File([large], "big.pdf")] } });
    await screen.findByText("the lease document is at most 5 MB");

    fireEvent.change(document, { target: { files: [pdf()] } });
    await ready();
    fireEvent.change(document, { target: { files: [] } });
    await screen.findByText("Choose the lease document first.");
    expect((screen.getByRole("button", { name: "Send for signing" }) as HTMLButtonElement).disabled).toBe(true);
    expect(posts(fetch)).toEqual([]);
  });

  it("shows who has signed, gives the links again, checks signatures and makes the lease", async () => {
    const fetch = server();
    render(<OffersCard />);

    const open = await screen.findByRole("listitem", { name: "Offer for Unit 2" });
    expect(open.textContent).toContain("Renewal at Property 1, Unit 2: 07/01/2027 – Month to month");
    expect(within(open).getByText(/Tenant 2 \(t2@example.com\): not signed yet/)).toBeTruthy();
    expect(within(open).getByText(/Signed by 1 of 2\./)).toBeTruthy();
    expect(within(open).getByRole("link", { name: "Lease (PDF)" }).getAttribute("href")).toBe(
      "/api/leasing/offers/o1/document",
    );
    // The offer that is a lease already isn't waiting.
    expect(screen.getAllByRole("listitem", { name: /^Offer for/ })).toHaveLength(1);

    fireEvent.click(within(open).getByRole("button", { name: "Signing links" }));
    expect(await within(open).findAllByTestId("signing-link")).toHaveLength(2);
    fireEvent.click(within(open).getByRole("button", { name: "Check signatures" }));
    fireEvent.click(within(open).getByRole("button", { name: "Make it the lease" }));

    expect(await screen.findByText("Property 1, Unit 2 has its lease from 07/01/2027.")).toBeTruthy();
    expect(posts(fetch).map(([path]) => path)).toEqual([
      "/api/leasing/offers/o1/signatures",
      "/api/leasing/offers/o1/complete",
    ]);
  });

  it("shows why the server won't make it the lease", async () => {
    server({ refuseCompletion: true });
    render(<OffersCard />);

    const open = await screen.findByRole("listitem", { name: "Offer for Unit 2" });
    fireEvent.click(within(open).getByRole("button", { name: "Make it the lease" }));

    expect((await screen.findByRole("alert")).textContent).toBe(
      "this offer is not signed by everyone yet",
    );
  });

  it("says when no offer is waiting", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => new Response("[]", { status: 200 })),
    );
    render(<OffersCard />);

    expect(await screen.findByText("No offers are waiting.")).toBeTruthy();
  });
});
