import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { Me } from "../api";
import type { Vendor } from "../vendors";
import { VendorsPage } from "./VendorsPage";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

const ME: Me = {
  kind: "staff", email: "staff@example.com", name: "Staff 1", person_id: "s1", pmc_id: "p1",
  pmc_name: "Synthetic PMC",
};

const NO_CONTACT = {
  email: null, phone: null, alternate_phone: null, address_line_1: null, address_line_2: null,
  city: null, state: null, postal_code: null,
};

const VENDORS: Vendor[] = [
  {
    id: "v1", name: "Vendor 1", w9_on_file: true,
    contact: { ...NO_CONTACT, phone: "555-555-0100", email: "vendor1@example.com" },
    insurance: [
      { coverage: "general liability", carrier: "Carrier A", policy_number: "GL-1", starts_on: "2025-07-01",
        expires_on: "2026-07-15", limit_amount: "1000000.00", standing: "expires in 15 days" },
      { coverage: "commercial auto", carrier: "Carrier C", policy_number: "AU-1", starts_on: "2025-01-01",
        expires_on: "2026-05-31", limit_amount: null, standing: "expired" },
    ],
  },
  { id: "v2", name: "Vendor 2", w9_on_file: false, insurance: [], contact: NO_CONTACT },
];

function server(vendors: Vendor[] = VENDORS, refuse = "") {
  const fetch = vi.fn(async (_path: string, init?: RequestInit) => {
    if (init?.method !== "POST") return new Response(JSON.stringify(vendors), { status: 200 });
    if (refuse) return new Response(JSON.stringify({ detail: refuse }), { status: 422 });
    return new Response(JSON.stringify({ id: "p9" }), { status: 201 });
  });
  vi.stubGlobal("fetch", fetch);
  return fetch;
}

describe("VendorsPage", () => {
  it("shows each vendor's W-9 and how each coverage stands", async () => {
    server();
    render(<VendorsPage me={ME} />);

    const first = await screen.findByRole("listitem", { name: "Vendor 1" });
    expect(within(first).getByText("W-9 on file")).toBeTruthy();
    const liability = within(first).getByRole("listitem", { name: "Vendor 1, general liability" });
    expect(liability.textContent).toContain("Carrier A GL-1, 2025-07-01 to 2026-07-15, limit $1,000,000.00");
    expect(within(liability).getByTestId("standing").textContent).toBe("expires in 15 days");
    expect(within(first).getByRole("listitem", { name: "Vendor 1, commercial auto" }).textContent).not.toContain("limit");
    const second = screen.getByRole("listitem", { name: "Vendor 2" });
    expect(second.textContent).toContain("no W-9");
    expect(second.textContent).toContain("No insurance on file.");
    expect(screen.getByRole("link", { name: "Vendor insurance (PDF)" }).getAttribute("href")).toMatch(
      /^\/api\/reports\/trust_report_vendor_insurance\?arg=\d{4}-\d\d-\d\d&format=pdf$/,
    );
  });

  it("records a policy, the limit only when given", async () => {
    const fetch = server();
    render(<VendorsPage me={ME} />);
    await screen.findByRole("listitem", { name: "Vendor 1" });

    fireEvent.change(screen.getByLabelText("Vendor"), { target: { value: "v2" } });
    fireEvent.change(screen.getByLabelText("Coverage"), { target: { value: "workers compensation" } });
    fireEvent.change(screen.getByLabelText("Carrier"), { target: { value: "Carrier B" } });
    fireEvent.change(screen.getByLabelText("Policy number"), { target: { value: "WC-9" } });
    fireEvent.change(screen.getByLabelText("Starts"), { target: { value: "2026-01-01" } });
    fireEvent.change(screen.getByLabelText("Expires"), { target: { value: "2026-12-31" } });
    fireEvent.click(screen.getByRole("button", { name: "Record policy" }));

    expect(await screen.findByText("Vendor 2's workers compensation policy WC-9 is recorded.")).toBeTruthy();
    const [path, init] = fetch.mock.calls.find(([, i]) => i?.method === "POST") ?? [];
    expect(path).toBe("/api/vendors/v2/insurance");
    expect(JSON.parse(String(init?.body))).toEqual({
      coverage: "workers compensation", carrier: "Carrier B", policy_number: "WC-9",
      starts_on: "2026-01-01", expires_on: "2026-12-31",
    });
    expect((screen.getByLabelText("Policy number") as HTMLInputElement).value).toBe("");
  });

  it("shows each vendor's contact details and saves them whole", async () => {
    const fetch = server();
    render(<VendorsPage me={ME} />);
    const first = await screen.findByRole("listitem", { name: "Vendor 1" });
    expect(within(first).getByRole("group", { name: "Contact details, Vendor 1" }).textContent).toContain(
      "vendor1@example.com",
    );
    const second = screen.getByRole("listitem", { name: "Vendor 2" });

    fireEvent.click(within(second).getByRole("button", { name: "Edit contact details" }));
    fireEvent.change(within(second).getByLabelText("Phone"), { target: { value: " 555-555-0101 " } });
    fireEvent.click(within(second).getByRole("button", { name: "Save contact details" }));

    expect(await within(second).findByText("Contact details saved.")).toBeTruthy();
    const [path, init] = fetch.mock.calls.find(([, i]) => i?.method === "POST") ?? [];
    expect(path).toBe("/api/vendors/v2/contact");
    expect(JSON.parse(String(init?.body))).toEqual({ ...NO_CONTACT, phone: "555-555-0101" });
  });

  it("shows a refusal, and says when there are no vendors", async () => {
    server(VENDORS, "a policy expires no earlier than it starts");
    render(<VendorsPage me={ME} />);
    await screen.findByRole("listitem", { name: "Vendor 1" });
    fireEvent.change(screen.getByLabelText("Carrier"), { target: { value: "Carrier B" } });
    fireEvent.change(screen.getByLabelText("Policy number"), { target: { value: "X" } });
    fireEvent.change(screen.getByLabelText("Expires"), { target: { value: "2020-01-01" } });
    fireEvent.click(screen.getByRole("button", { name: "Record policy" }));
    expect((await screen.findByRole("alert")).textContent).toBe("a policy expires no earlier than it starts");
    cleanup();

    server([]);
    render(<VendorsPage me={ME} />);
    expect((await screen.findByTestId("empty-state")).textContent).toContain("No vendors yet");
  });
});
