import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { OwnerBill } from "./OwnerBills";
import { OwnerBills } from "./OwnerBills";

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
};
const declined: OwnerBill = {
  ...waiting,
  id: "b2",
  reference: "PAINT-1",
  decision: "declined",
  decided_on: "2026-01-11",
  decided_by: "Owner 1, in the owner portal",
  reason: "Get a second quote",
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
  it("says what the owner's balance is short of, and what to send", async () => {
    const short = { ...waiting, id: "b3", reference: "GUTTER-1", amount: "300.00", decision: "approved" as const,
      decided_on: "2026-01-11", decided_by: "Owner 1", short: "120.00", needed: "120.00" };
    server([200, [{ ...waiting, short: "100.00", needed: "100.00" }, short]]);
    render(<OwnerBills />);
    expect((await screen.findByTestId("needed")).textContent).toBe("Your balance is $100.00 short of it: please send $100.00.");
    expect(screen.getByTestId("short-bill").textContent).toBe(
      "Vendor 1, GUTTER-1 ($300.00, Property 1, due 01/20/2026): your balance is $120.00 short of it. Please send"
        + " $120.00 so we can pay it. Nothing is paid or charged until it arrives.",
    );
  });

  it("shows a bill staff voided as void", async () => {
    server([200, [{ ...waiting, decision: "void", decided_on: "2026-01-12", decided_by: "Staff 1", reason: "Entered twice" }]]);
    render(<OwnerBills />);
    expect((await screen.findByRole("list", { name: "Decided" })).textContent).toBe(
      "voidVendor 1, ROOF-1 ($600.00) on 01/12/2026 by Staff 1. Entered twice",
    );
  });

  it("says the markup the owner's agreement adds to a bill", async () => {
    server([200, [{ ...waiting, markup: "60.00", markup_percent: "10" }]]);
    render(<OwnerBills />);
    expect((await screen.findByTestId("markup")).textContent).toBe(
      "Our markup of 10%, $60.00, as your management agreement sets, is taken with it.",
    );
  });

  it("lists the bills waiting for the owner and those decided", async () => {
    server([200, [waiting, declined]]);
    render(<OwnerBills />);
    const list = await screen.findByRole("list", { name: "Waiting for you" });
    expect(list.textContent).toContain("Vendor 1, ROOF-1: $600.00");
    expect(list.textContent).toContain("Over your approval limit of $500.00.");
    // No markup on it: nothing said of one.
    expect(screen.queryByTestId("markup")).toBeNull();
    // A decided bill: its decision as a badge, then what, when and by whom.
    const decided = screen.getByRole("list", { name: "Decided" });
    expect(decided.querySelector("[data-tone]")?.getAttribute("data-tone")).toBe("neutral");
    expect(decided.textContent).toBe(
      "declinedVendor 1, PAINT-1 ($600.00) on 01/11/2026 by Owner 1, in the owner portal. Get a second quote",
    );
  });

  it("approves a bill and shows it decided", async () => {
    const fetch = server([200, [waiting]], [201, null], [200, [{ ...waiting, decision: "approved" }]]);
    render(<OwnerBills />);
    const list = await screen.findByRole("list", { name: "Waiting for you" });
    fireEvent.click(within(list).getByRole("button", { name: "Approve" }));
    expect(await screen.findByText("Approved Vendor 1's ROOF-1.")).toBeTruthy();
    expect(fetch.mock.calls[1]?.[0]).toBe("/api/owner/bills/b1/approval");
    expect(await screen.findByText("No bill waits for your approval.")).toBeTruthy();
  });

  it("declines a bill only with a reason", async () => {
    const fetch = server([200, [waiting]], [201, null], [200, [declined]]);
    render(<OwnerBills />);
    fireEvent.click(await screen.findByRole("button", { name: "Decline…" }));
    const decline = screen.getByRole("button", { name: "Decline" }) as HTMLButtonElement;
    expect(decline.disabled).toBe(true);
    fireEvent.change(screen.getByLabelText("Why you decline it"), { target: { value: "Get a second quote" } });
    fireEvent.click(decline);
    expect(await screen.findByText("Declined Vendor 1's ROOF-1.")).toBeTruthy();
    expect(fetch.mock.calls[1]?.[0]).toBe("/api/owner/bills/b1/decline");
    expect(JSON.parse(String(fetch.mock.calls[1]?.[1]?.body))).toEqual({ reason: "Get a second quote" });
  });

  it("says why a decision was refused", async () => {
    server([200, [waiting]], [409, { detail: "this bill has its decision already" }]);
    render(<OwnerBills />);
    fireEvent.click(await screen.findByRole("button", { name: "Approve" }));
    expect((await screen.findByRole("alert")).textContent).toBe("this bill has its decision already");
  });
});
