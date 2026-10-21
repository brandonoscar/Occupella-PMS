import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { MyContactCard } from "./ContactCard";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

const NONE = {
  email: null, phone: null, alternate_phone: null, address_line_1: null, address_line_2: null,
  city: null, state: null, postal_code: null,
};

// The tenant's own contact details, as the server keeps them: none until they save some.
function server(refuse = "") {
  let contact: Record<string, string | null> = { ...NONE, email: "tenant@example.com" };
  const fetch = vi.fn(async (_path: string, init?: RequestInit) => {
    if (init?.method !== "POST") return new Response(JSON.stringify(contact), { status: 200 });
    if (refuse) return new Response(JSON.stringify({ detail: refuse }), { status: 422 });
    contact = JSON.parse(String(init.body));
    return new Response(null, { status: 204 });
  });
  vi.stubGlobal("fetch", fetch);
  return fetch;
}

describe("MyContactCard", () => {
  it("shows a tenant their contact details and saves their change to their own", async () => {
    const fetch = server();
    render(<MyContactCard name="Tenant 1" />);
    const card = await screen.findByRole("group", { name: "Contact details, Tenant 1" });
    expect(card.textContent).toContain("tenant@example.com");
    expect(card.textContent).toContain("You still sign in with the email you signed up with.");

    fireEvent.click(within(card).getByRole("button", { name: "Edit contact details" }));
    fireEvent.change(within(card).getByLabelText("Phone"), { target: { value: "555-555-0123" } });
    fireEvent.click(within(card).getByRole("button", { name: "Save contact details" }));

    expect(await within(card).findByText("Contact details saved.")).toBeTruthy();
    const posted = fetch.mock.calls.filter(([, init]) => init?.method === "POST");
    expect(posted.map(([path]) => path)).toEqual(["/api/tenant/contact"]);
    expect(JSON.parse(String(posted[0]![1]!.body))).toMatchObject({ email: "tenant@example.com", phone: "555-555-0123" });
    await vi.waitFor(() => expect(card.textContent).toContain("555-555-0123"));
  });

  it("says why a change was refused", async () => {
    server("a mailing address needs its street, city, state and ZIP code");
    render(<MyContactCard name="Tenant 1" />);
    const card = await screen.findByRole("group", { name: "Contact details, Tenant 1" });
    fireEvent.click(within(card).getByRole("button", { name: "Edit contact details" }));
    fireEvent.change(within(card).getByLabelText("City"), { target: { value: "Sampletown" } });
    fireEvent.click(within(card).getByRole("button", { name: "Save contact details" }));

    expect((await within(card).findByRole("alert")).textContent).toBe(
      "a mailing address needs its street, city, state and ZIP code",
    );
  });
});
