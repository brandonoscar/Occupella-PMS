import { describe, expect, it } from "vitest";
import type { PaymentSettings, Waiting } from "./payments";
import { autopayLine, autopayRunLine, feeLine, payable, settingsLine, waitingLine } from "./payments";

const SETTINGS: PaymentSettings = { online: true, ach_fee_payer: "company", partner: "fake", changed_at: null };
const WAITING: Waiting = {
  payment_id: "p1",
  lease_id: "l1",
  owner_id: null,
  memo: "Morgan Demo, Birch Flats Unit A",
  amount: "1200.00",
  what: "partner fee",
  company_owes: "5.00",
  bank_account_id: "b1",
  company_holds: "1.25",
  cover_needed: "3.75",
  choose_account: [],
};

describe("online payments", () => {
  it("says whether tenants pay online and who pays the fee", () => {
    expect(settingsLine(SETTINGS)).toBe("On: tenants and owners pay by bank for free, and the company pays the processing fee.");
    expect(settingsLine({ ...SETTINGS, ach_fee_payer: "tenant" })).toBe(
      "On: tenants pay by bank and pay the processing fee, shown before they pay. Owners pay by bank for free; the company pays theirs.",
    );
    expect(settingsLine({ ...SETTINGS, online: false })).toBe("Off: tenants pay by check, money order or in person, and owners send their money by check or bank transfer.");
    expect(settingsLine({ ...SETTINGS, partner: null })).toBe(
      "This server takes no payments online: its payments partner isn't set up.",
    );
  });

  it("shows the fee before the tenant pays", () => {
    expect(feeLine({ amount: "1000.00", tenant_fee: "0.00", total: "1000.00" })).toBe("$1,000.00, no fee");
    expect(feeLine({ amount: "500.00", tenant_fee: "4.03", total: "504.03" })).toBe(
      "$500.00 + $4.03 processing fee = $504.03",
    );
  });

  it("takes only an amount in dollars and cents, more than nothing", () => {
    for (const ok of ["1200", "1200.5", "1200.50", "0.01", " 12.00 "]) expect(payable(ok)).toBe(true);
    for (const no of ["", "0", "0.00", "-5", "12.345", "1,200.00", "abc", "1234567"]) expect(payable(no)).toBe(false);
  });

  it("says what waits for staff", () => {
    expect(waitingLine(WAITING)).toBe("The processor kept $5.00; the company's fee account holds $1.25");
    expect(waitingLine({ ...WAITING, what: "fee refund" })).toBe(
      "The tenant's $5.00 fee came back; the company's fee account holds $1.25",
    );
    expect(waitingLine({ ...WAITING, what: "payment", company_owes: null })).toContain("payment hold");
    expect(waitingLine({ ...WAITING, what: "return", company_owes: null })).toContain("owner's account");
  });

  it("says what a card dispute through Connect left waiting", () => {
    expect(waitingLine({ ...WAITING, what: "dispute fee", company_owes: "15.00" })).toBe(
      "A dispute cost a $15.00 fee; the company's fee account holds $1.25",
    );
    expect(waitingLine({ ...WAITING, what: "dispute won", company_owes: null })).toContain("not on the lease yet");
    expect(waitingLine({ ...WAITING, what: "dispute won", company_owes: null, owner_id: "o1" })).toContain(
      "not in the owner's account yet",
    );
  });

  it("says a payment started weeks ago with no word from the partner, to look up there (P-013)", () => {
    expect(waitingLine({ ...WAITING, what: "no word", company_owes: null, started_on: "2026-10-05" })).toBe(
      "Started online 10/05/2026, and the payments partner hasn't said since whether the bank paid:"
        + " look it up there. Its report brings it into the books; don't record it by hand.",
    );
  });

  it("says what waits of an owner's payment in its own words", () => {
    const owners = { ...WAITING, lease_id: null, owner_id: "o1", memo: "Demo Owner A / Birch Flats" };
    expect(waitingLine({ ...owners, what: "payment", company_owes: null })).toBe(
      "Paid online by the owner, not in the books yet: the books refused it",
    );
    expect(waitingLine({ ...owners, what: "return", company_owes: null })).toBe(
      "The owner's payment came back, not taken back out yet: their account holds too little",
    );
    expect(waitingLine(owners)).toBe("The processor kept $5.00; the company's fee account holds $1.25");
  });
});

describe("autopay wording", () => {
  const base = {
    id: "a1", tenant: "T", rule: "fixed" as const, amount: "1200.00", account: "Bank ••••1234",
    started_at: "2026-10-01T00:00:00Z", stopped_at: null, stopped: null, runs: [],
  };
  it("says what it pays, from where, or why it is off", () => {
    expect(autopayLine(base)).toBe("On: pays $1,200.00 on each rent due day, from Bank ••••1234");
    expect(autopayLine({ ...base, account: null, rule: "balance" })).toBe(
      "Waiting for a bank account to be saved: pays the balance due, up to $1,200.00",
    );
    expect(autopayLine({ ...base, stopped: "failed twice" })).toBe("Stopped: two payments in a row didn't go through");
    expect(autopayLine({ ...base, stopped: "account changed" })).toBe(
      "Stopped: the manager changed the Stripe account it was saved at. Set it up again.",
    );
    expect(autopayLine({ ...base, stopped: "something new" })).toBe("Off: something new");
  });
  it("reads a due date's payment with any fee, or why it took nothing", () => {
    expect(autopayRunLine({ due_on: "2026-11-01", amount: "1200.00", tenant_fee: "5.00", status: "started", skipped: null }))
      .toBe("11/01/2026 · $1,200.00 + $5.00 fee");
    expect(autopayRunLine({ due_on: "2026-11-01", amount: null, tenant_fee: null, status: "skipped", skipped: "payment hold" }))
      .toBe("11/01/2026 · Nothing taken: payment hold");
  });
});
