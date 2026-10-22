import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { holdBack } from "../holdBack";
import type { LeaseDetail } from "../rentals";
import { LeasingFeeForm, leasingFeeSaid } from "./LeasingFeeForm";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

const LEASE = { id: "l1", property: "Property 1", property_id: "pr1" } as LeaseDetail;
const accounts = [
  { account: "a1", owner: "Owner 1" },
  { account: "a2", owner: "Owner 2" },
];

describe("LeasingFeeForm", () => {
  it("takes the lease's leasing fee from the owner chosen", async () => {
    const fetch = vi.fn(async () => new Response(JSON.stringify(accounts), { status: 200 }));
    vi.stubGlobal("fetch", fetch);
    const record = vi.fn(async (_path: string, _body: object, _done: (taken: { amount: string }) => string) => true);
    render(<LeasingFeeForm lease={LEASE} record={record as never} />);

    fireEvent.change(await screen.findByLabelText("From owner"), { target: { value: "a2" } });
    fireEvent.change(screen.getByLabelText("Taken on"), { target: { value: "2026-10-16" } });
    fireEvent.click(screen.getByRole("button", { name: "Take leasing fee" }));

    expect(fetch.mock.calls[0]).toEqual(["/api/payables/owners?property=pr1", expect.anything()]);
    expect(record).toHaveBeenCalledWith(
      "/api/payables/leasing-fees", { lease_id: "l1", account: "a2", on: "2026-10-16" }, expect.any(Function),
    );
    expect(record.mock.calls[0]![2]({ amount: "750.00" })).toBe("A leasing fee of $750.00 is taken.");
  });

  it("offers the owners of the lease's property now, even when an earlier property's answer comes back last", async () => {
    const later = [{ account: "a3", owner: "Owner 3" }];
    vi.stubGlobal("fetch", vi.fn(async (path: string) =>
      new Response(JSON.stringify(path.endsWith("property=pr1") ? accounts : later))));
    const release = holdBack((path) => path.endsWith("property=pr1"));
    const { rerender } = render(<LeasingFeeForm lease={LEASE} record={vi.fn()} />);
    rerender(<LeasingFeeForm lease={{ ...LEASE, property: "Property 2", property_id: "pr2" }} record={vi.fn()} />);
    await screen.findByRole("option", { name: "Owner 3" });
    await release();

    expect(screen.getByRole("option", { name: "Owner 3" })).toBeTruthy();
    expect(screen.queryByRole("option", { name: "Owner 1" })).toBeNull();
  });

  it("says when no owner holds an account for the property, and when the accounts can't be read", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify([]), { status: 200 })));
    render(<LeasingFeeForm lease={LEASE} record={vi.fn()} />);
    expect(await screen.findByText("No owner holds an account for Property 1 yet.")).toBeTruthy();
    cleanup();

    vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({ detail: "sign in first" }), { status: 401 })));
    render(<LeasingFeeForm lease={LEASE} record={vi.fn()} />);
    expect((await screen.findByRole("alert")).textContent).toBe("sign in first");
  });

  it("says so when the agreement takes no leasing fee, rather than that $0.00 was taken", () => {
    expect(leasingFeeSaid("0.00")).toBe(
      "No leasing fee: the owner's agreement in force takes none, so none is due for this lease.",
    );
  });
});
