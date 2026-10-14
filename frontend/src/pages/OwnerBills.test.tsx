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
  decision: null,
  decided_on: null,
  decided_by: null,
  reason: null,
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
  it("lists the bills waiting for the owner and those decided", async () => {
    server([200, [waiting, declined]]);
    render(<OwnerBills />);
    const list = await screen.findByRole("list", { name: "Waiting for you" });
    expect(list.textContent).toContain("Vendor 1, ROOF-1: $600.00");
    expect(list.textContent).toContain("Over your approval limit of $500.00.");
    // A decided bill: its decision as a badge, then what, when and by whom.
    const decided = screen.getByRole("list", { name: "Decided" });
    expect(decided.querySelector("[data-tone]")?.getAttribute("data-tone")).toBe("neutral");
    expect(decided.textContent).toBe(
      "declinedVendor 1, PAINT-1 ($600.00) on 2026-01-11 by Owner 1, in the owner portal. Get a second quote",
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
