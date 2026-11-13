// Stripe Connect as the API sends it (src/pms/api/connect.py, docs/adr/0014-stripe-connect.md),
// and how it reads.
import { money } from "./money";

export type Method = "ach" | "card";
export type FeePayer = "company" | "tenant";

export const CONNECT = "/api/payments/connect";

// Company setup: the company's connected Stripe account.
export type ConnectView = {
  offered: boolean;
  platform: string | null;
  live: boolean;
  connected: boolean;
  account_ends: string | null;
  charges_enabled: boolean;
  payouts_enabled: boolean;
  details_submitted: boolean;
  requirements_due: string[];
  using: boolean;
  connected_at: string | null;
  connected_by: string | null;
  may_change: boolean;
  fees: { method: Method; words: string }[];
  card_fee_payer: FeePayer;
  card_fee_changed_at: string | null;
  rules: { state: string; says: string; card: "banned" | "capped" | "allowed" }[];
  card_max: string;
};

// A tenant's ways to pay a lease through Connect, and what each costs them for an amount.
export type MethodTerms = {
  method: Method;
  fee_payer: FeePayer;
  why_company: string | null;
  tenant_fee: string | null;
  total: string | null;
};
export type LeaseMethods = { connect: boolean; card_max: string; methods: MethodTerms[]; autopay: MethodTerms[] };
export type OwnerMethods = { connect: boolean; card_max: string };

export const METHOD_NAMES: Record<Method, string> = { ach: "Bank account", card: "Card" };

// Where the company's Stripe account stands, in a line.
export function connectLine(view: ConnectView): string {
  if (!view.offered) return "This server doesn't offer Stripe Connect.";
  if (!view.connected) return "Not connected. Connect your Stripe account to take rent by bank and card.";
  if (view.using) return `Connected: Stripe account ending in ${view.account_ends}. Payments go through it.`;
  if (!view.charges_enabled) return "Connected, but Stripe needs more before it takes payments. Continue setup.";
  return `Connected: Stripe account ending in ${view.account_ends}.`;
}

// "Occupella's fee: bank $0.50, card $0.50 a payment."
export function feesLine(view: ConnectView): string {
  const byMethod = view.fees.map((fee) => `${fee.method === "ach" ? "bank" : "card"} ${fee.words}`);
  return `Occupella's fee: ${byMethod.join(", ")} a payment, kept with Stripe's out of what reaches your trust account.`;
}

// What Stripe still needs, in Stripe's own words for each ("external_account", ...), or none.
export function requirementsLine(view: ConnectView): string {
  if (view.requirements_due.length === 0) return "";
  return `Stripe still needs: ${view.requirements_due.join(", ")}.`;
}

// The card fee choice in words.
export function cardFeeLine(view: ConnectView): string {
  return view.card_fee_payer === "tenant"
    ? "Tenants pay the card fee by credit card, shown before they pay, where their state allows it and never on autopay."
    : "The company pays the card fee.";
}

// "$1,000.00 + $30.00 card fee = $1,030.00", "$1,000.00, no fee", or "$1,000.00; the company pays the card fee".
export function methodLine(terms: MethodTerms, amount: string): string {
  if (terms.tenant_fee === null || terms.total === null) return "";
  const kind = terms.method === "card" ? "card fee" : "processing fee";
  if (terms.tenant_fee === "0.00") return `${money(amount)}, no fee`;
  return `${money(amount)} + ${money(terms.tenant_fee)} ${kind} = ${money(terms.total)}`;
}
