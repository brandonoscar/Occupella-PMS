import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { Me } from "../api";
import type { OwnerMoney } from "../payables";
import { OwnerDrawsPage } from "./OwnerDrawsPage";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

const ME: Me = {
  kind: "staff", email: "staff@example.com", name: "Staff 1", person_id: "s1", pmc_id: "p1",
  pmc_name: "Synthetic PMC",
};

const account = (n: number, available: string): OwnerMoney => ({
  account: `a${n}`, owner_id: `o${n}`, owner: `Owner ${n}`, property_id: `pr${n}`, property: `Property ${n}`,
  bank_account: "Operating", balance: "1200.00", reserve: "100.00", unpaid_bills: "300.00", available,
  has_agreement: true,
});
const OWNERS = [account(1, "800.00"), account(2, "0.00")];

function server(owners: OwnerMoney[] = OWNERS, refuse = "") {
  const fetch = vi.fn(async (_path: string, init?: RequestInit) => {
    if (init?.method !== "POST") return new Response(JSON.stringify(owners), { status: 200 });
    if (refuse) return new Response(JSON.stringify({ detail: refuse }), { status: 409 });
    return new Response(JSON.stringify({ amount: "800.00" }), { status: 201 });
  });
  vi.stubGlobal("fetch", fetch);
  return fetch;
}

describe("OwnerDrawsPage", () => {
  it("lists what each owner's account holds, keeps back and may pay out", async () => {
    server();
    render(<OwnerDrawsPage me={ME} />);

    const first = await screen.findByRole("row", { name: "Owner 1, Property 1" });
    expect(within(first).getAllByRole("cell").map((c) => c.textContent)).toEqual([
      "Owner 1", "Property 1", "$1,200.00", "$100.00", "$300.00", "$800.00",
    ]);
    expect(within(first).getByRole("link", { name: "Owner 1" }).getAttribute("href")).toBe("/owners/o1");
    expect(within(first).getByRole("link", { name: "Property 1" }).getAttribute("href")).toBe("/properties/pr1");
  });

  it("records a draw from the account chosen, once per request", async () => {
    const fetch = server();
    render(<OwnerDrawsPage me={ME} />);
    await screen.findByRole("row", { name: "Owner 1, Property 1" });
    expect(screen.getByTestId("draw-available").textContent).toBe("$800.00 available from Operating");

    fireEvent.change(screen.getByLabelText("Owner and property"), { target: { value: "a2" } });
    expect(screen.getByTestId("draw-available").textContent).toBe("$0.00 available from Operating");
    fireEvent.change(screen.getByLabelText("Owner and property"), { target: { value: "a1" } });
    fireEvent.change(screen.getByLabelText("Paid out on"), { target: { value: "2026-10-10" } });
    fireEvent.click(screen.getByRole("button", { name: "Record draw" }));
    expect(screen.getByRole("alertdialog").getAttribute("aria-label")).toBe(
      "Pay all $800.00 available out to Owner 1 from Operating on 10/10/2026?",
    );
    fireEvent.click(screen.getByRole("button", { name: "Confirm" }));

    expect(await screen.findByText("$800.00 paid out to Owner 1.")).toBeTruthy();
    const [path, init] = fetch.mock.calls.find(([, i]) => i?.method === "POST") ?? [];
    expect(path).toBe("/api/payables/draws");
    expect(JSON.parse(String(init?.body))).toMatchObject({ account: "a1", amount: null, on: "2026-10-10" });
  });

  it("shows why a draw was refused, says when there is no account, and when they can't be read", async () => {
    server(OWNERS, "nothing is available to draw");
    render(<OwnerDrawsPage me={ME} />);
    await screen.findByRole("row", { name: "Owner 1, Property 1" });
    fireEvent.click(screen.getByRole("button", { name: "Record draw" }));
    fireEvent.click(screen.getByRole("button", { name: "Confirm" }));
    expect((await screen.findByRole("alert")).textContent).toBe("nothing is available to draw");
    cleanup();

    server([]);
    render(<OwnerDrawsPage me={ME} />);
    expect((await screen.findByTestId("empty-state")).textContent).toContain("No owner has a property account yet.");
    cleanup();

    vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({ detail: "sign in first" }), { status: 401 })));
    render(<OwnerDrawsPage me={ME} />);
    expect((await screen.findByRole("alert")).textContent).toBe("sign in first");
  });
});
