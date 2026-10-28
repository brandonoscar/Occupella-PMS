import { describe, expect, it } from "vitest";
import type { DepositAsked, Hold, OpenCharge, RecurringCharge, RentLease } from "./rent";
import {
  balanceDue,
  bounceSummary,
  depositAskedLine,
  depositKindsLine,
  moveHome,
  PAYMENT_METHODS,
  earliestRentChange,
  holdLine,
  insuranceBadge,
  insuranceLine,
  methodLabel,
  rentChangeLine,
  tenantDepositLine,
  chargeLabel,
  dueDayLabel,
  dueDayLine,
  nextMonthFirst,
  ordinal,
  paymentSummary,
  recurringLabel,
  rentRunSummary,
  requestId,
} from "./rent";

describe("moves", () => {
  it("adds what the leases a lease's tenants moved from still owe to its balance, and names a home", () => {
    const lease = { owed: "100.10", carried: "1000.05" } as RentLease;
    expect(balanceDue(lease)).toBe("1100.15");
    expect(balanceDue({ ...lease, owed: "0.00", carried: "0.00" })).toBe("0.00");
    expect(moveHome({ lease_id: "l0", property: "Property 0", unit: "Unit 0", moved_on: "2026-04-01" }))
      .toBe("Property 0 · Unit 0");
  });
});

describe("renters insurance", () => {
  const ON_FILE = {
    required: true, minimum_liability: "100000.00", carrier: "Carrier A", policy_number: "RI-1",
    expires_on: "2027-01-31", liability_limit: "300000.00", standing: "current", below_minimum: false,
  };

  it("reads what the lease asks and the policy on file", () => {
    expect(insuranceLine(ON_FILE)).toBe(
      "Renters insurance required (at least $100,000.00 of liability): Carrier A RI-1 through 01/31/2027, $300,000.00 of liability",
    );
    expect(insuranceLine({ ...ON_FILE, required: false, minimum_liability: null, carrier: null,
      policy_number: null, expires_on: null, liability_limit: null, standing: "none on file" }))
      .toBe("Renters insurance not required: none on file");
  });

  it("marks a policy below the minimum or missing where required, and not where it isn't", () => {
    expect(insuranceBadge(ON_FILE)).toEqual({ word: "current", tone: "positive" });
    expect(insuranceBadge({ ...ON_FILE, below_minimum: true })).toEqual({ word: "below the minimum", tone: "danger" });
    expect(insuranceBadge({ ...ON_FILE, standing: "expires in 5 days" })).toEqual({ word: "expires in 5 days", tone: "caution" });
    expect(insuranceBadge({ ...ON_FILE, standing: "none on file" })).toEqual({ word: "none on file", tone: "danger" });
    expect(insuranceBadge({ ...ON_FILE, required: false, standing: "expired" })).toEqual({ word: "expired", tone: "neutral" });
  });
});

describe("rent changes", () => {
  it("reads a change, with its notice and the lease term that allows it", () => {
    const change = {
      id: "rc1", effective_on: "2026-12-01", monthly_rent: "1575.00", notice_given_on: "2026-10-15",
      served_by: "mail" as const, lease_terms: null,
    };
    expect(rentChangeLine(change)).toBe("From 12/01/2026: $1,575.00 a month (notice by mail on 10/15/2026)");
    expect(rentChangeLine({ ...change, served_by: "in person", lease_terms: "Section 4" })).toBe(
      "From 12/01/2026: $1,575.00 a month (notice in person on 10/15/2026; Section 4)",
    );
  });

  it("starts a change no sooner than a full month's notice given today allows", () => {
    expect(earliestRentChange(new Date("2026-10-01T12:00:00Z"))).toBe("2026-11-01");
    expect(earliestRentChange(new Date("2026-10-02T00:00:00Z"))).toBe("2026-12-01");
    expect(earliestRentChange(new Date("2026-12-15T00:00:00Z"))).toBe("2027-02-01");
  });
});

describe("rent due day", () => {
  it("says a day of the month as people say it", () => {
    expect([1, 2, 3, 4, 10, 11, 12, 13, 21, 22, 23, 28].map(ordinal)).toEqual([
      "1st", "2nd", "3rd", "4th", "10th", "11th", "12th", "13th", "21st", "22nd", "23rd", "28th",
    ]);
    expect(dueDayLabel(15)).toBe("Due on the 15th");
    expect(dueDayLine({ id: "d1", starts_on: "2026-11-01", due_day: 1 })).toBe(
      "From 11/01/2026: due on the 1st of each month",
    );
  });

  it("starts a new due day on the first of next month", () => {
    expect(nextMonthFirst(new Date("2026-10-01T00:00:00Z"))).toBe("2026-11-01");
    expect(nextMonthFirst(new Date("2026-12-31T23:00:00Z"))).toBe("2027-01-01");
  });
});

describe("rent", () => {
  it("says what a payment did", () => {
    expect(paymentSummary({ transfers: ["t"], to_owner: "1500.00", to_prepaid: "0.00" })).toBe(
      "$1,500.00 paid what is owed.",
    );
    expect(paymentSummary({ transfers: ["t", "u"], to_owner: "100.00", to_prepaid: "50.00" })).toBe(
      "$100.00 paid what is owed; $50.00 held as prepaid rent.",
    );
    expect(paymentSummary({ transfers: ["u"], to_owner: "0.00", to_prepaid: "50.00" })).toBe(
      "$50.00 held as prepaid rent.",
    );
  });
  it("says what a bounce did, with its NSF fee when there is one", () => {
    expect(bounceSummary("1500.00", { transfers: ["r"], nsf_fee: null })).toBe(
      "The $1,500.00 payment bounced and is undone.",
    );
    expect(bounceSummary("1500.00", { transfers: ["r", "s"], nsf_fee: "35.00" })).toBe(
      "The $1,500.00 payment bounced and is undone; a $35.00 NSF fee is charged.",
    );
  });
  it("reads an open charge", () => {
    const rent: OpenCharge = {
      id: "c", due_on: "2026-10-01", kind: "rent", memo: null, amount: "1500.00", open: "1500.00",
    };
    expect(chargeLabel(rent)).toBe("10/01/2026 Rent: $1,500.00 open");
    expect(chargeLabel({ ...rent, kind: "fee", open: "20.00", amount: "50.00" })).toBe(
      "10/01/2026 Fee: $20.00 open of $50.00",
    );
    expect(chargeLabel({ ...rent, memo: "Rent due 10/01/2026" })).toBe(
      "10/01/2026 Rent due 10/01/2026: $1,500.00 open",
    );
  });
  it("reads a recurring charge", () => {
    const pet: RecurringCharge = {
      id: "r", kind: "fee", memo: "Pet rent", amount: "35.00", starts_on: "2026-01-01", ends_on: null,
      charge_type: null,
    };
    expect(recurringLabel(pet)).toBe("Pet rent: $35.00 a month from 01/01/2026");
    // A fee of one of the company's charge types names it.
    expect(recurringLabel({ ...pet, charge_type: "Pet fees" })).toBe(
      "Pet rent (Pet fees): $35.00 a month from 01/01/2026",
    );
    expect(recurringLabel({ ...pet, kind: "credit", memo: "Concession", ends_on: "2026-07-01" })).toBe(
      "Concession: credit of $35.00 a month from 01/01/2026, stops 07/01/2026",
    );
  });
  it("says what security deposit a signed lease asked, what is held and what is still to receive", () => {
    const asked: DepositAsked = { asked: "1500.00", held: "1000.00", to_receive: "500.00" };
    expect(depositAskedLine(asked)).toBe("The signed lease asked $1,500.00: $1,000.00 held, $500.00 still to receive");
    expect(depositAskedLine({ ...asked, held: "1600.00", to_receive: "0.00" })).toBe(
      "The signed lease asked $1,500.00: all held",
    );
    expect(tenantDepositLine(asked)).toBe(
      "Security deposit your lease asks: $1,500.00. Held: $1,000.00; $500.00 still to give the manager.",
    );
    expect(tenantDepositLine({ ...asked, held: "1500.00", to_receive: "0.00" })).toBe(
      "Security deposit your lease asks: $1,500.00. Held: all of it.",
    );
  });
  it("says what the rent run charged", () => {
    expect(rentRunSummary({ rents: 1, recurring: 1, late_fees: 1 })).toBe(
      "1 rent, 1 recurring charge and 1 late fee charged.",
    );
    expect(rentRunSummary({ rents: 0, recurring: 2, late_fees: 3 })).toBe(
      "0 rents, 2 recurring charges and 3 late fees charged.",
    );
    // A run by hand says the emails it wrote, when it wrote any.
    expect(rentRunSummary({ rents: 1, recurring: 0, late_fees: 0, emails: 3 })).toBe(
      "1 rent, 0 recurring charges and 0 late fees charged. 3 emails written.",
    );
    expect(rentRunSummary({ rents: 1, recurring: 0, late_fees: 0, emails: 0 })).toBe(
      "1 rent, 0 recurring charges and 0 late fees charged.",
    );
    // And the leases whose prepaid rent paid what they owe, for a company that turned that on.
    expect(rentRunSummary({ rents: 2, recurring: 0, late_fees: 0, emails: 0, prepaid_used: 1 })).toBe(
      "2 rents, 0 recurring charges and 0 late fees charged. Prepaid rent paid what 1 lease owed.",
    );
    expect(rentRunSummary({ rents: 2, recurring: 0, late_fees: 0, emails: 1, prepaid_used: 2 })).toBe(
      "2 rents, 0 recurring charges and 0 late fees charged. Prepaid rent paid what 2 leases owed. 1 email written.",
    );
    expect(rentRunSummary({ rents: 1, recurring: 0, late_fees: 0, emails: 2, autopay: 1 })).toBe(
      "1 rent, 0 recurring charges and 0 late fees charged. 1 autopay payment started. 2 emails written.",
    );
    // And the fixed-term leases that ran out with no renewal, gone month to month.
    expect(rentRunSummary({ rents: 0, recurring: 0, late_fees: 0, emails: 0, leases_rolled_over: 2 })).toBe(
      "0 rents, 0 recurring charges and 0 late fees charged. 2 leases went month to month.",
    );
  });
  it("says a payment hold's days, why, who released it and what it let past", () => {
    const hold: Hold = { id: "h1", starts_on: "2026-10-01", reason: "Eviction filed", ends_on: null, released_by: null, allowed: 0, in_force: true };
    expect(holdLine(hold)).toBe("Since 10/01/2026: Eviction filed");
    expect(holdLine({ ...hold, ends_on: "2026-11-01", released_by: "Staff 1", allowed: 2 })).toBe(
      "Since 10/01/2026: Eviction filed, released from 11/01/2026 by Staff 1; 2 payments accepted past it",
    );
  });
  it("names each way a payment can come, a cashier's check among them", () => {
    expect(PAYMENT_METHODS.map((m) => m.label)).toEqual(["Check", "Cashier's check", "Money order", "EFT", "Cash"]);
    expect(methodLabel("cashier's check")).toBe("Cashier's check");
  });
  it("makes a new request id each time", () => {
    const one = requestId();
    expect(one).toMatch(/^[0-9a-f-]{36}$/);
    expect(requestId()).not.toBe(one);
  });
});

describe("depositKindsLine", () => {
  const tenant = (deposits: { kind: "security" | "pet" | "key"; amount: string }[]) =>
    ({ id: "t", name: "T", prepaid: "0.00", deposit: "0.00", deposits });

  it("says what the deposits are by kind, summed across the tenants, the security deposit first", () => {
    expect(depositKindsLine([
      tenant([{ kind: "key", amount: "50.00" }, { kind: "security", amount: "1000.00" }]),
      tenant([{ kind: "pet", amount: "300.00" }, { kind: "security", amount: "200.50" }]),
      tenant([{ kind: "pet", amount: "100.00" }]),
    ])).toBe("Security deposit $1,200.50 · Pet deposit $400.00 · Key deposit $50.00");
    expect(depositKindsLine([tenant([{ kind: "pet", amount: "300.00" }])])).toBe("Pet deposit $300.00");
  });

  it("says nothing when every deposit is a security deposit, or none is held", () => {
    expect(depositKindsLine([tenant([{ kind: "security", amount: "1000.00" }]), tenant([])])).toBe("");
    expect(depositKindsLine([])).toBe("");
    // A kind that holds nothing is not named.
    expect(depositKindsLine([tenant([{ kind: "security", amount: "10.00" }, { kind: "pet", amount: "0.00" }])])).toBe("");
  });
});
