// Rent paid online as the API sends it (src/pms/api/online_payments.py), and how it reads.
import type { BadgeTone } from "./components/Badge";
import { money } from "./money";

export type FeePayer = "company" | "tenant";
export type OnlineStatus = "started" | "paid" | "failed" | "returned" | "expired";
export type OnlinePayment = {
  id: string;
  started_at: string;
  amount: string;
  tenant_fee: string;
  status: OnlineStatus;
};
// Whether the tenant may pay a lease online now (and if not, why), what it owes and who pays the
// partner's fee.
export type PayView = {
  available: boolean;
  why_not: string | null;
  owed: string;
  fee_payer: FeePayer;
  payments: OnlinePayment[];
};
export type FeeQuote = { amount: string; tenant_fee: string; total: string };
export type Started = { id: string; url: string };
// A company's choices, and the server's partner (null: this server takes no payments online).
export type PaymentSettings = {
  online: boolean;
  ach_fee_payer: FeePayer;
  partner: string | null;
  changed_at: string | null;
};
export type WaitingWhat = "payment" | "return" | "partner fee" | "fee refund";
export type Waiting = {
  payment_id: string;
  lease_id: string;
  memo: string;
  amount: string;
  what: WaitingWhat;
  company_owes: string | null;
  bank_account_id: string;
  company_holds: string;
  // What the company deposits from its own account to cover all that waits in this trust bank
  // account: what it owes there less what its fee account holds.
  cover_needed: string;
};

export const PAYMENT_SETTINGS = "/api/payments/settings";

export const STATUS: Record<OnlineStatus, { label: string; tone: BadgeTone }> = {
  started: { label: "Waiting for your bank", tone: "neutral" },
  paid: { label: "Paid", tone: "positive" },
  failed: { label: "Didn't go through", tone: "danger" },
  returned: { label: "Came back", tone: "danger" },
  expired: { label: "Not finished", tone: "neutral" },
};

// "On: tenants pay by bank for free; the company pays the processing fee", or why it is off.
export function settingsLine(settings: PaymentSettings): string {
  if (settings.partner === null) return "This server takes no payments online: its payments partner isn't set up.";
  if (!settings.online) return "Off: tenants pay by check, money order or in person.";
  return settings.ach_fee_payer === "company"
    ? "On: tenants pay by bank for free, and the company pays the processing fee."
    : "On: tenants pay by bank and pay the processing fee, shown before they pay.";
}

// "$1,000.00, no fee" or "$1,000.00 + $5.00 processing fee = $1,005.00".
export function feeLine(quote: FeeQuote): string {
  if (quote.tenant_fee === "0.00") return `${money(quote.amount)}, no fee`;
  return `${money(quote.amount)} + ${money(quote.tenant_fee)} processing fee = ${money(quote.total)}`;
}

// Whether `text` reads as an amount: dollars and at most two places of cents, more than nothing.
// (The API refuses anything over 100,000.00.)
export function payable(text: string): boolean {
  return /^\d{1,6}(\.\d{1,2})?$/.test(text.trim()) && /[1-9]/.test(text);
}

// What waits for staff, in words.
export function waitingLine(item: Waiting): string {
  switch (item.what) {
    case "payment":
      return `Paid online, not in the books yet: the lease is on a payment hold, or the books refused it`;
    case "return":
      return `Came back, not undone yet: the owner's account holds too little`;
    case "partner fee":
      return `The processor kept ${money(item.company_owes)}; the company's fee account holds ${money(item.company_holds)}`;
    case "fee refund":
      return `The tenant's ${money(item.company_owes)} fee came back; the company's fee account holds ${money(item.company_holds)}`;
  }
}
