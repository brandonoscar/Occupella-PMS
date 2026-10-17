import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { Me } from "../api";
import type { Offer, Signer } from "../offers";
import { leave } from "../router";
import { RenewalsCard } from "./RenewalsCard";

vi.mock("../router", () => ({ leave: vi.fn() }));

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.mocked(leave).mockClear();
});

const ME: Me = {
  kind: "tenant",
  email: "Tenant1@example.com",
  name: "Tenant 1",
  person_id: "t1",
  pmc_id: "p1",
  pmc_name: "Synthetic PMC",
};

const RENEWAL: Offer = {
  id: "o1",
  kind: "renewal",
  application_id: null,
  renews_lease_id: "l1",
  property_id: "p1",
  property: "Property 1",
  unit_id: "u1",
  unit: "Unit 1",
  starts_on: "2027-07-01",
  ends_on: "2028-06-30",
  monthly_rent: "1250.00",
  document_title: "Lease renewal",
  offered_at: "2026-10-11T18:00:00Z",
  sent: true,
  signers: [
    { position: 1, name: "Tenant 1", email: "tenant1@example.com", role: "tenant", signed_at: null },
    { position: 2, name: "Tenant 2", email: "tenant2@example.com", role: "tenant", signed_at: "2026-10-11T19:00:00Z" },
  ],
  lease_id: null,
  security_deposit: null,
  recurring_charges: [],
  status: "partially signed",
  void: null,
};

function server(renewals: Offer[], link: Response = new Response(JSON.stringify({ url: "http://x/esign#t" }))) {
  const fetch = vi.fn(async (_path: string, init?: RequestInit) =>
    init?.method === "POST" ? link : new Response(JSON.stringify(renewals), { status: 200 }),
  );
  vi.stubGlobal("fetch", fetch);
  return fetch;
}

describe("RenewalsCard", () => {
  it("shows the renewal and its lease, and sends the tenant to sign it", async () => {
    const fetch = server([RENEWAL]);
    render(<RenewalsCard me={ME} />);

    const renewal = await screen.findByRole("listitem", { name: "Renewal of Unit 1" });
    expect(renewal.textContent).toContain(
      "Synthetic PMC offers a renewal of Property 1, Unit 1: 07/01/2027 – 06/30/2028, $1,250.00 a month.",
    );
    expect(within(renewal).getByText("Signing: Tenant 1, Tenant 2 (signed)")).toBeTruthy();
    expect(within(renewal).getByRole("link", { name: "Read the lease (PDF)" }).getAttribute("href")).toBe(
      "/api/tenant/renewals/o1/document",
    );
    fireEvent.click(within(renewal).getByRole("button", { name: "Sign" }));

    await vi.waitFor(() => expect(leave).toHaveBeenCalledWith("http://x/esign#t"));
    expect(fetch).toHaveBeenCalledWith(
      "/api/tenant/renewals/o1/signing-link",
      expect.objectContaining({ method: "POST" }),
    );
  });

  it("says when the tenant has signed, and when someone else signs", async () => {
    const [mine, theirs] = RENEWAL.signers as [Signer, Signer];
    server([
      { ...RENEWAL, signers: [{ ...mine, signed_at: "2026-10-11T20:00:00Z" }, theirs] },
      { ...RENEWAL, id: "o2", unit: "Unit 2", signers: [theirs] },
    ]);
    render(<RenewalsCard me={ME} />);

    expect((await screen.findByTestId("renewal-signed")).textContent).toMatch(/^You signed /);
    const other = screen.getByRole("listitem", { name: "Renewal of Unit 2" });
    expect(within(other).getByText("Someone else on the lease signs this one.")).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Sign" })).toBeNull();
  });

  it("shows why there is no signing link", async () => {
    server([RENEWAL], new Response(JSON.stringify({ detail: "no e-sign partner is set up" }), { status: 503 }));
    render(<RenewalsCard me={ME} />);

    fireEvent.click(await screen.findByRole("button", { name: "Sign" }));

    expect((await screen.findByRole("alert")).textContent).toBe("no e-sign partner is set up");
    expect(leave).not.toHaveBeenCalled();
  });

  it("shows nothing without a renewal", async () => {
    const fetch = server([]);
    const { container } = render(<RenewalsCard me={ME} />);

    await vi.waitFor(() => expect(fetch).toHaveBeenCalled());
    expect(container.textContent).toBe("");
  });
});
