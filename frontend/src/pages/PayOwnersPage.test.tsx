import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { Me } from "../api";
import { holdBack } from "../holdBack";
import type { OwnerMoney } from "../payables";
import { PayOwnersPage } from "./PayOwnersPage";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  window.history.pushState(null, "", "/");
  window.localStorage.clear();
});

const ME: Me = {
  kind: "staff", email: "staff@example.com", name: "Staff 1", person_id: "s1", pmc_id: "p1", pmc_name: "Synthetic PMC",
};
const line = (n: number, available: string): OwnerMoney => ({
  account: `a${n}`, owner_id: `o${n}`, owner: `Owner ${n}`, property_id: `pr${n}`, property: `Property ${n}`,
  bank_account: "Operating", balance: "1000.00", reserve: "100.00", unpaid_bills: "0.00", available,
  has_agreement: true,
});
const OWNERS = [line(1, "900.00"), line(2, "250.50"), line(3, "0.00")];

function server(answers: Record<string, unknown> = {}, refuse: Record<string, string> = {}) {
  const fetch = vi.fn(async (path: string, init?: RequestInit) => {
    if (init?.method !== "POST") {
      const read = path in answers ? answers[path] : path.startsWith("/api/payables/owners") ? OWNERS : { groups: [], properties: [] };
      return new Response(JSON.stringify(read), { status: 200 });
    }
    if (path in refuse) return new Response(JSON.stringify({ detail: refuse[path] }), { status: 409 });
    return new Response(JSON.stringify(answers[path] ?? []), { status: 201 });
  });
  vi.stubGlobal("fetch", fetch);
  return fetch;
}

const posted = (fetch: ReturnType<typeof server>) =>
  fetch.mock.calls
    .filter(([, init]) => init?.method === "POST")
    .map(([path, init]) => [path, JSON.parse(String(init?.body))]);

describe("PayOwnersPage", () => {
  it("pays the owners chosen, each what is available unless changed, by check or EFT, after asking", async () => {
    const fetch = server({
      "/api/payables/draws/batch": [
        { account: "a1", owner: "Owner 1", property: "Property 1", amount: "900.00", transfer_id: "t1" },
        { account: "a2", owner: "Owner 2", property: "Property 2", amount: "200.25", transfer_id: "t2" },
      ],
    });
    render(<PayOwnersPage me={ME} />);
    await screen.findByRole("row", { name: "Owner 1, Property 1" });

    expect(screen.getByRole("button", { name: "Choose owners to pay" })).toHaveProperty("disabled", true);
    expect(screen.getByLabelText("Pay Owner 3, Property 3")).toHaveProperty("disabled", true);
    fireEvent.click(screen.getByLabelText("Choose every owner with money available"));
    fireEvent.change(screen.getByLabelText("Amount to pay Owner 2, Property 2"), { target: { value: "200.25" } });
    fireEvent.change(screen.getByLabelText("Sent by"), { target: { value: "EFT" } });
    fireEvent.change(screen.getByLabelText("Paid out on"), { target: { value: "2026-10-16" } });
    fireEvent.click(screen.getByRole("button", { name: "Pay $1,100.25" }));

    const confirm = screen.getByRole("alertdialog");
    expect(confirm.textContent).toContain("Pay $1,100.25 to 2 owner accounts by EFT on 10/16/2026?");
    fireEvent.click(within(confirm).getByRole("button", { name: "Confirm" }));

    expect(await screen.findByText("2 payouts of $1,100.25 recorded, by EFT.")).toBeTruthy();
    const [[path, body]] = posted(fetch) as [[string, Record<string, unknown>]];
    expect(path).toBe("/api/payables/draws/batch");
    expect(body).toMatchObject({
      on: "2026-10-16",
      method: "EFT",
      payouts: [{ account: "a1", amount: "900.00" }, { account: "a2", amount: "200.25" }],
    });
    // Paid, the choice clears, and the next run is a new request.
    expect(screen.getByRole("button", { name: "Choose owners to pay" })).toBeTruthy();
  });

  it("chooses and unchooses one owner, and narrows to a property", async () => {
    const fetch = server({ "/api/property-groups": { groups: [], properties: [{ id: "pr2", name: "Property 2" }] } });
    render(<PayOwnersPage me={ME} />);
    await screen.findByRole("row", { name: "Owner 2, Property 2" });

    fireEvent.click(screen.getByLabelText("Pay Owner 2, Property 2"));
    expect(screen.getByRole("button", { name: "Pay $250.50" })).toBeTruthy();
    fireEvent.click(screen.getByLabelText("Pay Owner 2, Property 2"));
    expect(screen.queryByLabelText("Amount to pay Owner 2, Property 2")).toBeNull();
    fireEvent.click(screen.getByLabelText("Choose every owner with money available"));
    fireEvent.click(screen.getByLabelText("Choose every owner with money available"));
    expect(screen.getByRole("button", { name: "Choose owners to pay" })).toBeTruthy();

    const picker = screen.getByRole("combobox", { name: "Properties shown" });
    await within(picker).findByRole("option", { name: "Property 2" });
    fireEvent.change(picker, { target: { value: "property:pr2" } });
    await vi.waitFor(() => expect(fetch.mock.calls.at(-1)?.[0]).toBe("/api/payables/owners?property=pr2"));
  });

  it("says why the payouts were refused, and pays none", async () => {
    server({}, { "/api/payables/draws/batch": "Owner 2, Property 2: only $250.50 is available to draw" });
    render(<PayOwnersPage me={ME} />);
    await screen.findByRole("row", { name: "Owner 2, Property 2" });
    fireEvent.click(screen.getByLabelText("Pay Owner 2, Property 2"));
    fireEvent.click(screen.getByRole("button", { name: "Pay $250.50" }));
    fireEvent.click(within(screen.getByRole("alertdialog")).getByRole("button", { name: "Confirm" }));

    expect((await screen.findByRole("alert")).textContent).toBe("Owner 2, Property 2: only $250.50 is available to draw");
    expect(screen.getByRole("button", { name: "Pay $250.50" })).toBeTruthy();
  });

  it("says when no owner has an account here, and when the accounts can't be read", async () => {
    server({ "/api/payables/owners": [] });
    render(<PayOwnersPage me={ME} />);
    expect((await screen.findByTestId("empty-state")).textContent).toContain("No owner has a property account here.");
    cleanup();

    vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({ detail: "sign in first" }), { status: 401 })));
    render(<PayOwnersPage me={ME} />);
    expect((await screen.findAllByRole("alert"))[0]?.textContent).toBe("sign in first");
  });

  it("shows the owners of the property group picked now, even when an earlier group's answer comes back last", async () => {
    server({
      "/api/property-groups": { groups: [{ id: "g1", name: "North", properties: [] }, { id: "g2", name: "South", properties: [] }], properties: [] },
      "/api/payables/owners": [line(3, "0.00")],
      "/api/payables/owners?group=g1": [line(1, "900.00")],
      "/api/payables/owners?group=g2": [line(2, "250.50")],
    });
    const release = holdBack((path) => path.includes("group=g1"));
    render(<PayOwnersPage me={ME} />);
    await screen.findByRole("row", { name: "Owner 3, Property 3" });
    const picker = screen.getByRole("combobox", { name: "Properties shown" });
    await within(picker).findByRole("option", { name: "South" });

    fireEvent.change(picker, { target: { value: "group:g1" } });
    fireEvent.change(picker, { target: { value: "group:g2" } });
    await screen.findByRole("row", { name: "Owner 2, Property 2" });
    await release();

    expect(screen.getByRole("row", { name: "Owner 2, Property 2" })).toBeTruthy();
    expect(screen.queryByRole("row", { name: "Owner 1, Property 1" })).toBeNull();
  });
});
