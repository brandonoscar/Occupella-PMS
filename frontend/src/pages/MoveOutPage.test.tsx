import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { Me } from "../api";
import type { MoveOut } from "../moveout";
import { MoveOutPage } from "./MoveOutPage";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

const ME: Me = {
  kind: "staff", email: "staff@example.com", name: "Staff 1", person_id: "s1", pmc_id: "p1",
  pmc_name: "Synthetic PMC", roles: ["disburse"],
};

const ENDED: MoveOut = {
  lease_id: "l1", property: "Property 1", unit: "Unit 1", tenants: ["Tenant 1"],
  starts_on: "2026-01-01", ends_on: "2026-06-30", owed: "350.00", held: "1200.00",
  open_charges: [{ id: "c1", due_on: "2026-06-30", kind: "fee", memo: "Broken window", open: "350.00" }],
  owner_accounts: [{ id: "a1", owner: "Owner 1" }, { id: "a2", owner: "Owner 2" }],
  settled: null,
};
const OPEN: MoveOut = { ...ENDED, lease_id: "l2", unit: "Unit 2", ends_on: null, open_charges: [], owner_accounts: [{ id: "a1", owner: "Owner 1" }] };
const DONE: MoveOut = {
  ...ENDED, lease_id: "l3", unit: "Unit 3", held: "0.00",
  settled: { held: "1200.00", kept: "350.00", returned: "850.00", returned_on: "2026-07-05" },
};

function server(refuse: Record<string, string> = {}) {
  const fetch = vi.fn(async (path: string, init?: RequestInit) => {
    if (init?.method !== "POST") return new Response(JSON.stringify([ENDED, OPEN, DONE]), { status: 200 });
    if (path in refuse) return new Response(JSON.stringify({ detail: refuse[path] }), { status: 409 });
    if (path.endsWith("/end")) return new Response(null, { status: 204 });
    if (path.endsWith("/return")) return new Response(JSON.stringify({ returned: "850.00" }), { status: 201 });
    return new Response(JSON.stringify({ id: "c9" }), { status: 201 });
  });
  vi.stubGlobal("fetch", fetch);
  return fetch;
}

const posted = (fetch: ReturnType<typeof server>) =>
  fetch.mock.calls
    .filter(([, init]) => init?.method === "POST")
    .map(([path, init]) => [path, JSON.parse(String(init?.body))]);

describe("MoveOutPage", () => {
  it("returns an ended lease's deposit, keeping what it owes for the chosen owner's account", async () => {
    const fetch = server();
    render(<MoveOutPage me={ME} />);
    expect((await screen.findByLabelText("Owed, Property 1 / Unit 1: Tenant 1")).textContent)
      .toBe("2026-06-30 fee: Broken window: $350.00");
    expect(screen.getByText(/Keeps \$350.00 for what the lease owes; \$850.00 goes back/)).toBeTruthy();

    fireEvent.change(screen.getAllByLabelText("Paid on")[0]!, { target: { value: "2026-07-05" } });
    fireEvent.change(screen.getByLabelText("Kept money to"), { target: { value: "a2" } });
    fireEvent.click(screen.getByRole("button", { name: "Return the deposit" }));

    expect(await screen.findByText("Returned $850.00 to the tenants; the rest paid what the lease owed.")).toBeTruthy();
    const [[path, body]] = posted(fetch) as [[string, { request_id: string; on: string; owner_account: string }]];
    expect(path).toBe("/api/move-out/l1/return");
    expect(body.on).toBe("2026-07-05");
    expect(body.owner_account).toBe("a2");
    expect(body.request_id).toMatch(/^[0-9a-f-]{36}$/);
  });

  it("ends a lease and charges cleaning or damage", async () => {
    const fetch = server();
    render(<MoveOutPage me={ME} />);
    const open = await screen.findByRole("region", { name: "Property 1 / Unit 2: Tenant 1" });
    expect(within(open).getByText("The deposit goes back once the lease has ended.")).toBeTruthy();
    // Each lease says where its move-out stands; a running lease folds its forms away.
    expect(within(open).getByText("In effect").closest("[data-tone]")?.getAttribute("data-tone")).toBe("neutral");
    expect(within(open).getByText("Move-out for Unit 2").tagName).toBe("SUMMARY");
    const ended = screen.getByRole("region", { name: "Property 1 / Unit 1: Tenant 1" });
    expect(within(ended).getByText("Ended, deposit to return")).toBeTruthy();
    expect(within(ended).queryByText("Move-out for Unit 1")).toBeNull();
    const done = screen.getByRole("region", { name: "Property 1 / Unit 3: Tenant 1" });
    expect(within(done).getByText("Deposit returned").closest("[data-tone]")?.getAttribute("data-tone")).toBe("positive");

    fireEvent.change(screen.getAllByLabelText("Last day")[1]!, { target: { value: "2026-07-31" } });
    fireEvent.click(screen.getAllByRole("button", { name: "End the lease" })[1]!);
    expect(await screen.findByText("The lease ends on 2026-07-31.")).toBeTruthy();
    fireEvent.change(screen.getAllByLabelText("Cleaning or damage")[0]!, { target: { value: " Carpet cleaning " } });
    fireEvent.change(screen.getAllByLabelText("Amount")[0]!, { target: { value: "120.00" } });
    fireEvent.click(screen.getAllByRole("button", { name: "Charge it" })[0]!);
    expect(await screen.findByText("Charged $120.00: Carpet cleaning.")).toBeTruthy();

    expect(posted(fetch)).toEqual([
      ["/api/move-out/l2/end", { ends_on: "2026-07-31" }],
      ["/api/move-out/l1/deductions", { amount: "120.00", due_on: "2026-06-30", memo: " Carpet cleaning " }],
    ]);
    expect((screen.getAllByLabelText("Amount")[0] as HTMLInputElement).value).toBe("");
  });

  it("shows a returned deposit with its statement, a refusal, and an empty or unreadable page", async () => {
    server({ "/api/move-out/l1/return": "Clerk 1 may not pay out money (the disburse role)" });
    render(<MoveOutPage me={ME} />);
    expect((await screen.findByLabelText("Returned, Property 1 / Unit 3: Tenant 1")).textContent)
      .toBe("Returned on 2026-07-05: kept $350.00 of $1,200.00, $850.00 back to the tenants.");
    expect(screen.getByRole("link", { name: "Statement for the tenants (PDF)" }).getAttribute("href"))
      .toBe("/api/reports/trust_report_deposit_return?arg=l3&format=pdf");
    fireEvent.click(screen.getByRole("button", { name: "Return the deposit" }));
    expect(await screen.findByText("Clerk 1 may not pay out money (the disburse role)")).toBeTruthy();
    cleanup();

    vi.stubGlobal("fetch", vi.fn(async () => new Response("[]", { status: 200 })));
    render(<MoveOutPage me={ME} />);
    expect(await screen.findByText("No lease holds a security deposit.")).toBeTruthy();
    cleanup();

    vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({ detail: "sign in first" }), { status: 401 })));
    render(<MoveOutPage me={ME} />);
    expect((await screen.findAllByRole("alert"))[0]?.textContent).toBe("sign in first");
  });
});
