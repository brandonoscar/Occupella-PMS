import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { OwnerBill } from "./OwnerBills";
import { OwnerBills, balanceAfter, billTotal } from "./OwnerBills";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

const waiting: OwnerBill = {
  id: "b1",
  vendor: "Vendor 1",
  reference: "ROOF-1",
  property: "Property 1",
  bill_date: "2026-01-10",
  due_on: "2026-01-20",
  amount: "600.00",
  memo: "Roof repair",
  approval_limit: "500.00",
  markup: null,
  markup_percent: null,
  decision: null,
  decided_on: null,
  decided_by: null,
  reason: null,
  short: null,
  needed: null,
  balance: "3290.00",
  name_needed: false,
  status: "waiting",
};
const declined: OwnerBill = {
  ...waiting,
  id: "b2",
  reference: "PAINT-1",
  decision: "declined",
  decided_on: "2026-01-11",
  decided_by: "Owner 1, in the owner portal",
  reason: "Get a second quote",
  status: "declined",
};

// Answers each request in turn, recording what was asked.
function server(...answers: [number, unknown][]) {
  const fetch = vi.fn(async (_path: string, _init?: RequestInit) => {
    const [status, body] = answers.shift() ?? [500, { detail: "no more answers" }];
    return new Response(status === 201 ? null : JSON.stringify(body), { status });
  });
  vi.stubGlobal("fetch", fetch);
  return fetch;
}

describe("OwnerBills", () => {
  // A row of the table as its cells read.
  const cells = (row: HTMLElement) => within(row).getAllByRole("cell").map((cell) => cell.textContent);

  it("lists each bill with where it stands: waiting, approved, paid, short on funds and declined", async () => {
    const approved = { ...waiting, id: "b4", reference: "TAP-1", amount: "650.00", decision: "approved" as const, status: "approved" as const };
    const paid = { ...approved, id: "b5", reference: "DOOR-1", status: "paid" as const };
    const short = { ...approved, id: "b3", reference: "GUTTER-1", amount: "300.00", short: "120.00", needed: "120.00",
      status: "short" as const };
    server([200, [waiting, approved, paid, short, declined]]);
    render(<OwnerBills />);
    const table = await screen.findByRole("table", { name: "Your bills" });
    expect(within(table).getAllByRole("columnheader").map((h) => h.textContent)).toEqual(
      ["Vendor", "Bill #", "Property", "Amount", "Due", "Status", "Action"],
    );
    expect(cells(within(table).getByRole("row", { name: "Vendor 1 ROOF-1" }))).toEqual(
      ["Vendor 1Roof repair", "ROOF-1", "Property 1", "$600.00", "01/20/2026", "Waiting for you", "Approve…Decline…"],
    );
    expect(cells(within(table).getByRole("row", { name: "Vendor 1 TAP-1" }))[5]).toBe("Approved");
    expect(cells(within(table).getByRole("row", { name: "Vendor 1 DOOR-1" }))[5]).toBe("Paid");
    expect(cells(within(table).getByRole("row", { name: "Vendor 1 GUTTER-1" }))[5]).toBe("Short on fundsSend $120.00");
    expect(cells(within(table).getByRole("row", { name: "Vendor 1 PAINT-1" }))[5]).toBe("DeclinedGet a second quote");
    // Only a bill waiting for them has anything to press.
    expect(within(table).getAllByRole("button", { name: "Approve…" })).toHaveLength(1);
    expect(screen.getByTestId("waiting-count").textContent).toBe("1 bill waits for your approval.");
  });

  it("says what a bill waiting for them is short of", async () => {
    server([200, [{ ...waiting, short: "100.00", needed: "100.00" }]]);
    render(<OwnerBills />);
    expect((await screen.findByTestId("needed")).textContent).toBe("Send $100.00");
  });

  it("shows a bill staff voided as void", async () => {
    server([200, [{ ...waiting, decision: "void", decided_on: "2026-01-12", decided_by: "Staff 1", reason: "Entered twice",
      status: "void" }]]);
    render(<OwnerBills />);
    const row = await screen.findByRole("row", { name: "Vendor 1 ROOF-1" });
    expect(cells(row)[5]).toBe("Void");
  });

  it("shows what a bill takes with the markup the owner's agreement adds", async () => {
    server([200, [{ ...waiting, markup: "60.00", markup_percent: "10" }]]);
    render(<OwnerBills />);
    expect(cells(await screen.findByRole("row", { name: "Vendor 1 ROOF-1" }))[3]).toBe("$660.00incl. $60.00 markup");
  });

  it("says when no bill needs them", async () => {
    server([200, []]);
    render(<OwnerBills />);
    expect((await screen.findByTestId("empty-state")).textContent).toContain("No bills need you.");
  });

  it("works out what the bill takes and what the account holds after", () => {
    expect(billTotal({ amount: "600.00", markup: null })).toBe("600.00");
    expect(billTotal({ amount: "600.10", markup: "60.01" })).toBe("660.11");
    expect(balanceAfter({ amount: "24000.00", markup: null, balance: "3290.00" })).toBe("-20710.00");
    expect(balanceAfter({ amount: "600.00", markup: "60.00", balance: "3290.00" })).toBe("2630.00");
  });

  it("asks first, showing the bill and the balance before and after, then approves it", async () => {
    const fetch = server([200, [waiting]], [201, null], [200, [{ ...waiting, decision: "approved", status: "approved" }]]);
    render(<OwnerBills />);
    const table = await screen.findByRole("table", { name: "Your bills" });
    fireEvent.click(within(table).getByRole("button", { name: "Approve…" }));
    const dialog = screen.getByRole("dialog", { name: "Approve this bill?" });
    expect(dialog.textContent).toContain("VendorVendor 1Bill #ROOF-1PropertyProperty 1Amount$600.00");
    expect(dialog.textContent).toContain("Balance now$3,290.00Balance after$2,690.00");
    // Nothing is sent until they confirm, and no name is asked under the company's amount.
    expect(fetch.mock.calls).toHaveLength(1);
    expect(within(dialog).queryByLabelText("Type your full name to approve")).toBeNull();
    fireEvent.click(within(dialog).getByRole("button", { name: "Approve" }));
    expect(await screen.findByText("Approved Vendor 1's ROOF-1.")).toBeTruthy();
    expect(fetch.mock.calls[1]?.[0]).toBe("/api/owner/bills/b1/approval");
    expect(JSON.parse(String(fetch.mock.calls[1]?.[1]?.body))).toEqual({});
    expect(await screen.findByText("No bill waits for your approval.")).toBeTruthy();
  });

  it("takes the owner's typed name over the company's amount, and says why one is refused", async () => {
    const big = { ...waiting, amount: "24000.00", name_needed: true };
    const fetch = server([200, [big]], [422, { detail: "That name doesn't match the name on your account." }]);
    render(<OwnerBills />);
    fireEvent.click(await screen.findByRole("button", { name: "Approve…" }));
    const dialog = screen.getByRole("dialog", { name: "Approve this bill?" });
    expect(dialog.textContent).toContain("Balance after($20,710.00)");
    const approve = within(dialog).getByRole("button", { name: "Approve" }) as HTMLButtonElement;
    expect(approve.disabled).toBe(true);
    fireEvent.change(within(dialog).getByLabelText("Type your full name to approve"), { target: { value: "Someone" } });
    fireEvent.click(approve);
    expect((await within(dialog).findByRole("alert")).textContent).toBe("That name doesn't match the name on your account.");
    expect(JSON.parse(String(fetch.mock.calls[1]?.[1]?.body))).toEqual({ typed_name: "Someone" });
  });

  it("sends nothing when the owner cancels", async () => {
    const fetch = server([200, [waiting]]);
    render(<OwnerBills />);
    fireEvent.click(await screen.findByRole("button", { name: "Approve…" }));
    fireEvent.click(within(screen.getByRole("dialog")).getByRole("button", { name: "Cancel" }));
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(fetch.mock.calls).toHaveLength(1);
  });

  it("declines a bill only with a reason", async () => {
    const fetch = server([200, [waiting]], [201, null], [200, [declined]]);
    render(<OwnerBills />);
    fireEvent.click(await screen.findByRole("button", { name: "Decline…" }));
    const dialog = screen.getByRole("dialog", { name: "Decline this bill?" });
    expect(dialog.textContent).toContain("Vendor 1, ROOF-1, $600.00 for Property 1. It is never paid from your account.");
    const decline = within(dialog).getByRole("button", { name: "Decline" }) as HTMLButtonElement;
    expect(decline.disabled).toBe(true);
    fireEvent.change(within(dialog).getByLabelText("Why you decline it"), { target: { value: "Get a second quote" } });
    fireEvent.click(decline);
    expect(await screen.findByText("Declined Vendor 1's ROOF-1.")).toBeTruthy();
    expect(fetch.mock.calls[1]?.[0]).toBe("/api/owner/bills/b1/decline");
    expect(JSON.parse(String(fetch.mock.calls[1]?.[1]?.body))).toEqual({ reason: "Get a second quote" });
  });

  it("says why a decision was refused", async () => {
    server([200, [waiting]], [409, { detail: "This bill was already approved or declined." }]);
    render(<OwnerBills />);
    fireEvent.click(await screen.findByRole("button", { name: "Approve…" }));
    fireEvent.click(within(screen.getByRole("dialog")).getByRole("button", { name: "Approve" }));
    expect((await screen.findByRole("alert")).textContent).toBe("This bill was already approved or declined.");
  });
});
