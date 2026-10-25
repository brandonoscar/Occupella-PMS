import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { FromApplication } from "../applying";
import { TenantApplicationCards } from "./TenantApplicationCards";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

const APPLIED: FromApplication = {
  emergency_contact: { name: "Contact 1", phone: "555-555-0160", recorded_at: "2026-10-25T00:00:00Z" },
  application: {
    application_id: "a1",
    submitted_at: "2026-10-01T14:00:00Z",
    details: {
      phone: "555-555-0101",
      address: { line_1: "1 Sample Street", line_2: null, city: "Sampleton", state: "ST", postal_code: "00001" },
      previous_landlord: null,
      employer: "Employer 1",
      emergency_contact: { name: "Contact 1", phone: "555-555-0160", recorded_at: "2026-10-01T14:00:00Z" },
      income_proofs: ["d1"],
    },
  },
};

function server(found: FromApplication, refuse?: string) {
  let now = found;
  const fetch = vi.fn(async (_path: string, init?: RequestInit) => {
    if (init?.method === "POST") {
      if (refuse) return new Response(JSON.stringify({ detail: refuse }), { status: 422 });
      const body = JSON.parse(String(init.body));
      now = { ...now, emergency_contact: { ...body, recorded_at: "2026-10-25T01:00:00Z" } };
    }
    return new Response(JSON.stringify(now), { status: 200 });
  });
  vi.stubGlobal("fetch", fetch);
  return fetch;
}

describe("TenantApplicationCards", () => {
  it("shows the emergency contact and what the tenant gave when applying", async () => {
    server(APPLIED);
    render(<TenantApplicationCards id="t1" name="Tenant 1" />);
    expect((await screen.findByTestId("emergency-contact")).textContent).toBe("Contact 1, 555-555-0160");
    const gave = screen.getByLabelText("What Tenant 1 gave when applying");
    expect(within(gave).getByText("1 Sample Street, Sampleton, ST 00001")).toBeTruthy();
    expect(within(gave).getByText("Employer 1")).toBeTruthy();
    expect(within(gave).getByText("None given")).toBeTruthy();
    expect(screen.getByRole("link", { name: "Open the application" }).getAttribute("href")).toBe("/applicants/a1");
  });

  it("adds an emergency contact for a tenant who never applied", async () => {
    const fetch = server({ emergency_contact: null, application: null });
    render(<TenantApplicationCards id="t1" name="Tenant 1" />);
    expect((await screen.findByTestId("emergency-contact")).textContent).toBe("None on file");
    expect(screen.queryByText("From their application")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Add emergency contact" }));
    const panel = screen.getByRole("dialog", { name: "Emergency contact, Tenant 1" });
    fireEvent.change(within(panel).getByLabelText("Name"), { target: { value: "Contact 2" } });
    fireEvent.change(within(panel).getByLabelText("Phone"), { target: { value: "555-555-0171" } });
    fireEvent.click(within(panel).getByRole("button", { name: "Save" }));

    expect(await screen.findByText("Saved Tenant 1's emergency contact.")).toBeTruthy();
    expect((await screen.findByTestId("emergency-contact")).textContent).toBe("Contact 2, 555-555-0171");
    const posted = fetch.mock.calls.filter(([, init]) => init?.method === "POST");
    expect(posted.map(([path, init]) => [path, JSON.parse(String(init?.body))])).toEqual([
      ["/api/tenants/t1/emergency-contact", { name: "Contact 2", phone: "555-555-0171" }],
    ]);
  });

  it("says why a contact was refused", async () => {
    server({ emergency_contact: null, application: null }, "a phone is digits");
    render(<TenantApplicationCards id="t1" name="Tenant 1" />);
    fireEvent.click(await screen.findByRole("button", { name: "Add emergency contact" }));
    const panel = screen.getByRole("dialog", { name: "Emergency contact, Tenant 1" });
    fireEvent.change(within(panel).getByLabelText("Name"), { target: { value: "Contact 2" } });
    fireEvent.change(within(panel).getByLabelText("Phone"), { target: { value: "x" } });
    fireEvent.click(within(panel).getByRole("button", { name: "Save" }));
    expect(await within(panel).findByText("a phone is digits")).toBeTruthy();
  });
});
