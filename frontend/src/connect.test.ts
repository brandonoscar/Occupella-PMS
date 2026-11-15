import { describe, expect, it } from "vitest";
import type { ConnectView } from "./connect";
import { cardFeeLine, connectLine, feesLine, methodLine, requirementsLine } from "./connect";

const VIEW: ConnectView = {
  offered: true,
  platform: "fake_connect",
  live: false,
  connected: true,
  account_ends: "a1b2",
  charges_enabled: true,
  payouts_enabled: true,
  details_submitted: true,
  requirements_due: [],
  using: true,
  connected_at: "2026-11-13T15:00:00Z",
  connected_by: "Staff 1",
  may_change: true,
  fees: [
    { method: "ach", words: "$0.50" },
    { method: "card", words: "$0.50" },
  ],
  card_fee_payer: "company",
  card_fee_changed_at: null,
  rules: [],
  card_max: "10000.00",
};

describe("Stripe Connect in words", () => {
  it("says where the company's Stripe account stands", () => {
    expect(connectLine(VIEW)).toBe("Connected: Stripe account ending in a1b2. Payments go through it.");
    expect(connectLine({ ...VIEW, using: false, charges_enabled: false })).toBe(
      "Connected, but Stripe needs more before it takes payments. Continue setup.",
    );
    expect(connectLine({ ...VIEW, connected: false, using: false })).toBe(
      "Not connected. Connect your Stripe account to take rent by bank and card.",
    );
    expect(connectLine({ ...VIEW, offered: false })).toBe("This server doesn't offer Stripe Connect.");
  });

  it("says Occupella's fee, what Stripe still needs and who pays the card fee", () => {
    expect(feesLine(VIEW)).toBe(
      "Occupella's fee: bank $0.50, card $0.50 a payment, kept with Stripe's out of what reaches your trust account.",
    );
    expect(requirementsLine(VIEW)).toBe("");
    expect(requirementsLine({ ...VIEW, requirements_due: ["external_account"] })).toBe(
      "Stripe still needs: external_account.",
    );
    expect(cardFeeLine(VIEW)).toBe("The company pays the card fee.");
    expect(cardFeeLine({ ...VIEW, card_fee_payer: "tenant" })).toContain("by credit card");
  });

  it("shows what a tenant pays by each way before they go", () => {
    const card = { method: "card" as const, fee_payer: "tenant" as const, why_company: null, tenant_fee: "30.00", total: "1030.00" };
    expect(methodLine(card, "1000.00")).toBe("$1,000.00 + $30.00 card fee = $1,030.00");
    expect(methodLine({ ...card, method: "ach", tenant_fee: "5.50", total: "1005.50" }, "1000.00")).toBe(
      "$1,000.00 + $5.50 processing fee = $1,005.50",
    );
    expect(methodLine({ ...card, fee_payer: "company", tenant_fee: "0.00", total: "1000.00" }, "1000.00")).toBe(
      "$1,000.00, no fee",
    );
    expect(methodLine({ ...card, tenant_fee: null, total: null }, "1000.00")).toBe("");
  });
});
