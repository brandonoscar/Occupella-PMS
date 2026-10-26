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
  lease_id: "l1", property: "Property 1", unit: "Unit 1", tenants: ["Tenant 1"], tenant_ids: ["t1"],
  starts_on: "2026-01-01", ends_on: "2026-06-30", owed: "350.00", carried: "0.00", held: "1200.00",
  open_charges: [{ id: "c1", due_on: "2026-06-30", kind: "fee", memo: "Broken window", open: "350.00" }],
  owner_accounts: [{ id: "a1", owner: "Owner 1", percent: null }, { id: "a2", owner: "Owner 2", percent: null }],
  settled: null, return_by: "2026-07-14",
  fees: [{ id: "c1", due_on: "2026-06-30", memo: "Broken window", amount: "350.00", receipts: [] }],
};
const OPEN: MoveOut = { ...ENDED, lease_id: "l2", unit: "Unit 2", ends_on: null, open_charges: [], owner_accounts: [{ id: "a1", owner: "Owner 1", percent: null }] };
const DONE: MoveOut = {
  ...ENDED, lease_id: "l3", unit: "Unit 3", held: "0.00", return_by: null,
  settled: { held: "1200.00", kept: "350.00", returned: "850.00", returned_on: "2026-07-05" },
  fees: [{ id: "c3", due_on: "2026-06-30", memo: "Carpet cleaning", amount: "350.00",
    receipts: [{ id: "d1", title: "CleanCo invoice" }] }],
};

function server(refuse: Record<string, string> = {}, leases: MoveOut[] = [ENDED, OPEN, DONE]) {
  const fetch = vi.fn(async (path: string, init?: RequestInit) => {
    if (init?.method !== "POST") return new Response(JSON.stringify(leases), { status: 200 });
    if (path in refuse) return new Response(JSON.stringify({ detail: refuse[path] }), { status: 409 });
    if (path.endsWith("/end")) return new Response(null, { status: 204 });
    if (path.endsWith("/return")) return new Response(JSON.stringify({ returned: "850.00" }), { status: 201 });
    if (path.endsWith("/statement/send")) {
      return new Response(JSON.stringify({ emailed: [{ name: "Tenant 1", to: "t1@example.com" }], without_email: [] }), { status: 201 });
    }
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
  it("counts what the lease its tenants moved from still owes, naming its home", async () => {
    server({}, [{
      ...ENDED, carried: "500.00",
      open_charges: [
        { id: "c0", due_on: "2026-03-01", kind: "rent", memo: "March rent", open: "500.00", home: "Property 0, Unit 0" },
        ...ENDED.open_charges,
      ],
    }]);
    render(<MoveOutPage me={ME} />);
    expect((await screen.findByLabelText("Owed, Property 1 / Unit 1: Tenant 1")).textContent).toBe(
      "03/01/2026 rent: March rent (Property 0, Unit 0): $500.00" + "06/30/2026 fee: Broken window: $350.00",
    );
    expect(document.body.textContent).toContain("owes $350.00 and $500.00 at the home they moved from");
    expect(screen.getByText(/Keeps \$850.00 for what the lease owes; \$350.00 goes back/)).toBeTruthy();
  });

  it("returns an ended lease's deposit, keeping what it owes for the chosen owner's account", async () => {
    const fetch = server();
    render(<MoveOutPage me={ME} />);
    expect((await screen.findByLabelText("Owed, Property 1 / Unit 1: Tenant 1")).textContent)
      .toBe("06/30/2026 fee: Broken window: $350.00");
    expect(screen.getByText(/Keeps \$350.00 for what the lease owes; \$850.00 goes back/)).toBeTruthy();

    fireEvent.change(screen.getAllByLabelText("Paid on")[0]!, { target: { value: "2026-07-05" } });
    fireEvent.change(screen.getByLabelText("Kept money to"), { target: { value: "a2" } });
    fireEvent.click(screen.getByRole("button", { name: "Return the deposit" }));
    const ask = screen.getByRole("alertdialog");
    expect(ask.getAttribute("aria-label")).toBe(
      "Return the deposit on 07/05/2026: keep $350.00 for what the lease owes and pay $850.00 back to the tenants?"
        + " This records what you paid at the bank; it sends no money or email.",
    );
    expect(posted(fetch)).toHaveLength(0);
    fireEvent.click(within(ask).getByRole("button", { name: "Confirm" }));

    expect(await screen.findByText("Returned $850.00 to the tenants; the rest paid what the lease owed.")).toBeTruthy();
    const [[path, body]] = posted(fetch) as [[string, { request_id: string; on: string; owner_account: string }]];
    expect(path).toBe("/api/move-out/l1/return");
    expect(body.on).toBe("2026-07-05");
    expect(body.owner_account).toBe("a2");
    expect(body.request_id).toMatch(/^[0-9a-f-]{36}$/);
  });

  it("keeps what a co-owned property's lease owes for its owners by their shares, choosing no account", async () => {
    const fetch = server({}, [{
      ...ENDED, owner_accounts: [{ id: "a1", owner: "Owner 1", percent: "60" }, { id: "a2", owner: "Owner 2", percent: "40" }],
    }]);
    render(<MoveOutPage me={ME} />);
    expect((await screen.findByTestId("kept-by-ownership")).textContent)
      .toBe("Kept money goes to the owners by their shares: Owner 1 60% · Owner 2 40%");
    expect(screen.queryByLabelText("Kept money to")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Return the deposit" }));
    fireEvent.click(within(screen.getByRole("alertdialog")).getByRole("button", { name: "Confirm" }));
    await vi.waitFor(() => expect(posted(fetch)).toHaveLength(1));
    expect((posted(fetch)[0]![1] as { owner_account: string | null }).owner_account).toBe(null);
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
    expect(await screen.findByText("The lease ends on 07/31/2026.")).toBeTruthy();
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
      .toBe("Returned on 07/05/2026: kept $350.00 of $1,200.00, $850.00 back to the tenants.");
    expect(screen.getByRole("link", { name: "Statement for Tenant 1 (PDF)" }).getAttribute("href"))
      .toBe("/api/move-out/l3/statement?tenant_id=t1");
    fireEvent.click(screen.getByRole("button", { name: "Return the deposit" }));
    fireEvent.click(screen.getByRole("button", { name: "Confirm" }));
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

  it("shows when a deposit is due back and keeps a receipt for a fee that needs one", async () => {
    const fetch = server({}, [ENDED]);
    render(<MoveOutPage me={ME} />);
    // Its day passed: the badge says it was due.
    expect(await screen.findByText("Was due back 07/14/2026")).toBeTruthy();
    const fees = screen.getByRole("list", { name: "Fees and receipts, Property 1 / Unit 1: Tenant 1" });
    expect(within(fees).getByText("Receipt needed")).toBeTruthy();
    expect(within(fees).getByText("Broken window, due 06/30/2026: $350.00")).toBeTruthy();

    const add = within(fees).getByRole("button", { name: "Add receipt" });
    expect((add as HTMLButtonElement).disabled).toBe(true);
    const picked = new File([new Uint8Array([0x25, 0x50, 0x44, 0x46, 0x2d])], "glazier-invoice.pdf", { type: "application/pdf" });
    fireEvent.change(within(fees).getByLabelText("Receipt for Broken window"), { target: { files: [picked] } });
    await vi.waitFor(() => expect((add as HTMLButtonElement).disabled).toBe(false));
    fireEvent.click(add);

    expect(await screen.findByText("Kept glazier-invoice as a receipt for Broken window.")).toBeTruthy();
    expect(posted(fetch)).toEqual([["/api/move-out/l1/fees/c1/receipts",
      { title: "glazier-invoice", content_type: "application/pdf", content_base64: "JVBERi0=" }]]);
  });

  it("refuses a receipt that isn't a PDF, JPEG or PNG before sending it", async () => {
    server({}, [ENDED]);
    render(<MoveOutPage me={ME} />);
    const input = await screen.findByLabelText("Receipt for Broken window");
    fireEvent.change(input, { target: { files: [new File(["x"], "note.txt", { type: "text/plain" })] } });
    expect(await screen.findByText("A receipt is a PDF, JPEG or PNG.")).toBeTruthy();
  });

  it("emails a returned deposit's statement and links each receipt", async () => {
    const fetch = server({}, [DONE]);
    render(<MoveOutPage me={ME} />);
    expect((await screen.findByRole("link", { name: "CleanCo invoice" })).getAttribute("href")).toBe("/api/documents/d1/file");
    expect(screen.queryByText("Receipt needed")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Email the statement" }));
    expect(await screen.findByText("Emailed Tenant 1 at t1@example.com.")).toBeTruthy();
    expect(posted(fetch)).toEqual([["/api/move-out/l3/statement/send", {}]]);
  });
});
