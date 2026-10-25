import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { DepositInterest } from "../depositInterest";
import type { RentLease } from "../rent";
import { DepositInterestForm } from "./DepositInterestForm";
import type { Recorder } from "./LeaseForms";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.useRealTimers();
});

const LEASE = { id: "l1" } as unknown as RentLease;

const DUE: DepositInterest = {
  annual_percent: "1.5",
  rate_from: "2026-01-01",
  from_on: "2026-01-01",
  through: "2026-10-22",
  deposits: [{ tenant: "Tenant 1", held: "1000.00", interest: "11.71" }],
  due: "11.71",
  in_effect: true,
  returned: false,
  paid: [],
  owner_accounts: [{ id: "a1", owner: "Owner 1" }],
};

// Answers each read with `answers(path)`, and keeps the paths read.
function server(answers: (path: string) => DepositInterest) {
  const fetch = vi.fn(async (path: string) => new Response(JSON.stringify(answers(path)), { status: 200 }));
  vi.stubGlobal("fetch", fetch);
  return fetch;
}

function recorder(answer: unknown) {
  const said: string[] = [];
  const record = vi.fn(async (_path: string, _body: object, done: (a: never) => string) => {
    said.push(done(answer as never));
    return true;
  });
  return { record: record as unknown as Recorder, calls: record.mock.calls, said };
}

describe("DepositInterestForm", () => {
  it("shows what each deposit earned at the property's rate and credits it against the rent", async () => {
    vi.useFakeTimers({ toFake: ["Date"], now: new Date("2026-10-22T12:00:00Z") });
    server(() => DUE);
    const paid = { from_on: "2026-01-01", through_on: "2026-10-22", amount: "11.71", paid_as: "rent_credit" };
    const { record, calls, said } = recorder(paid);
    render(<DepositInterestForm lease={LEASE} record={record} />);

    expect(await screen.findByTestId("interest-rate")).toHaveProperty("textContent", "1.5% a year (from 01/01/2026)");
    expect(screen.getByRole("list", { name: "Deposits" }).textContent).toBe("Tenant 1: $1,000.00 held, $11.71 earned");
    expect(screen.getByTestId("interest-due").textContent).toBe("Due for 01/01/2026 to 10/22/2026: $11.71");
    // One owner's account: nothing to choose.
    expect(screen.queryByLabelText("From the owner's account")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Pay interest" }));

    await vi.waitFor(() => expect(said).toEqual(["$11.71 credited against the rent, 01/01/2026 to 10/22/2026."]));
    expect(calls[0]![0]).toBe("/api/rent/leases/l1/deposit-interest");
    expect(calls[0]![1]).toMatchObject({ through: "2026-10-22", paid_as: "rent_credit", on: "2026-10-22" });
    expect(calls[0]![1]).not.toHaveProperty("owner_account");
  });

  it("adds it to the deposit of a lease that ended, from the owner's account named", async () => {
    vi.useFakeTimers({ toFake: ["Date"], now: new Date("2026-10-22T12:00:00Z") });
    const ended = {
      ...DUE,
      in_effect: false,
      owner_accounts: [{ id: "a1", owner: "Owner 1" }, { id: "a2", owner: "Owner 2" }],
      paid: [{ from_on: "2025-01-01", through_on: "2025-12-31", amount: "15.00", paid_as: "rent_credit" as const, paid_on: "2026-01-05" }],
    };
    server(() => ended);
    const { record, calls } = recorder({ from_on: "2026-01-01", through_on: "2026-10-22", amount: "11.71", paid_as: "deposit" });
    render(<DepositInterestForm lease={LEASE} record={record} />);

    const as = await screen.findByLabelText("Pay it as") as HTMLSelectElement;
    // A lease no longer in effect takes no rent credit.
    expect([...as.options].map((o) => o.textContent)).toEqual(["Added to the deposit"]);
    expect(screen.getByRole("list", { name: "Interest paid" }).textContent)
      .toBe("$15.00 credited against the rent, 01/01/2025 to 12/31/2025, on 01/05/2026");
    fireEvent.change(screen.getByLabelText("From the owner's account"), { target: { value: "a2" } });
    fireEvent.click(screen.getByRole("button", { name: "Pay interest" }));

    await vi.waitFor(() => expect(calls).toHaveLength(1));
    expect(calls[0]![1]).toMatchObject({ paid_as: "deposit", owner_account: "a2" });
  });

  it("reads it again through another day, keeping only that day's answer, and pays nothing when none is due", async () => {
    vi.useFakeTimers({ toFake: ["Date"], now: new Date("2026-10-22T12:00:00Z") });
    const fetch = server((path) => (path.endsWith("2026-02-01") ? { ...DUE, through: "2026-02-01", due: "0.00" } : DUE));
    render(<DepositInterestForm lease={LEASE} record={recorder(null).record} />);
    await screen.findByTestId("interest-due");

    fireEvent.change(screen.getByLabelText("Interest through"), { target: { value: "2026-02-01" } });

    await vi.waitFor(() => expect(screen.getByTestId("interest-due").textContent).toBe("Due for 01/01/2026 to 02/01/2026: $0.00"));
    expect(fetch.mock.calls.map(([path]) => path)).toEqual([
      "/api/rent/leases/l1/deposit-interest?through=2026-10-22",
      "/api/rent/leases/l1/deposit-interest?through=2026-02-01",
    ]);
    expect((screen.getByRole("button", { name: "Pay interest" }) as HTMLButtonElement).disabled).toBe(true);
  });

  it("says when the deposit went back, and when the property has no terms", async () => {
    server(() => ({ ...DUE, returned: true, rate_from: null, annual_percent: "0" }));
    render(<DepositInterestForm lease={LEASE} record={recorder(null).record} />);
    expect(await screen.findByText("The deposit went back: its interest was paid before it did.")).toBeTruthy();
    expect(screen.getByTestId("interest-rate").textContent).toBe("No deposit interest");
    expect(screen.queryByRole("button", { name: "Pay interest" })).toBeNull();
  });

  it("says why it couldn't read it", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({ detail: "no such lease" }), { status: 404 })));
    render(<DepositInterestForm lease={LEASE} record={recorder(null).record} />);
    expect(await screen.findByText("no such lease")).toBeTruthy();
  });
});
