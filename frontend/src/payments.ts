// Rent paid online as the API sends it (src/pms/api/online_payments.py), and how it reads.
import type { BadgeTone } from "./components/Badge";
import { money, usDate } from "./money";

export type FeePayer = "company" | "tenant";
export type OnlineStatus = "started" | "paid" | "failed" | "returned" | "expired";
export type OnlinePayment = {
  id: string;
  started_at: string;
  amount: string;
  tenant_fee: string;
  status: OnlineStatus;
};
// Why a tenant or owner can't pay online now: the company takes no payment online ("off"), or the
// lease is on a payment hold ("paused"). The portal says it in its own words.
export type WhyNot = "off" | "paused";
// The company's own words on how to pay it (Company setup, How to pay us), or what to do without.
export const PAUSED = "Online payments are paused for this lease. Contact your property manager.";
export const NO_HOW_TO_PAY = "Contact your property manager to pay.";
// Whether the tenant may pay a lease online now (and if not, why, with how the company says to
// pay it), what it owes and who pays the partner's fee.
export type PayView = {
  available: boolean;
  why_not: WhyNot | null;
  how_to_pay: string | null;
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
export type WaitingWhat = "payment" | "return" | "partner fee" | "fee refund" | "no word";
export type Waiting = {
  payment_id: string;
  // A tenant's payment's lease, or the owner of an owner's payment (src/pms/api/owner_payments.py).
  lease_id: string | null;
  owner_id: string | null;
  memo: string;
  amount: string;
  what: WaitingWhat;
  company_owes: string | null;
  bank_account_id: string;
  company_holds: string;
  // The day a payment the partner has said nothing of was started (what "no word", P-013).
  started_on?: string | null;
  // What the company deposits from its own account to cover all that waits in this trust bank
  // account: what it owes there less what its fee account holds.
  cover_needed: string;
  // The owner accounts staff choose among to record a payment whose property has several and no
  // ownership shares (docs/edge-cases.md P-040), or none.
  choose_account: { account_id: string; owner: string }[];
};

export const PAYMENT_SETTINGS = "/api/payments/settings";

export const STATUS: Record<OnlineStatus, { label: string; tone: BadgeTone }> = {
  started: { label: "Waiting for your bank", tone: "neutral" },
  paid: { label: "Paid", tone: "positive" },
  failed: { label: "Didn't go through", tone: "danger" },
  returned: { label: "Came back", tone: "danger" },
  expired: { label: "Not finished", tone: "neutral" },
};

// "On: tenants and owners pay by bank for free; the company pays the processing fee", or why it is
// off. An owner's processing fee is always the company's (the founder, 2026-11-02).
export function settingsLine(settings: PaymentSettings): string {
  if (settings.partner === null) return "This server takes no payments online: its payments partner isn't set up.";
  if (!settings.online) return "Off: tenants pay by check, money order or in person, and owners send their money by check or bank transfer.";
  return settings.ach_fee_payer === "company"
    ? "On: tenants and owners pay by bank for free, and the company pays the processing fee."
    : "On: tenants pay by bank and pay the processing fee, shown before they pay. Owners pay by bank for free; the company pays theirs.";
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
  const owners = item.owner_id !== null;
  switch (item.what) {
    case "payment":
      if (item.choose_account.length > 0) {
        return "Paid online, not in the books yet: its property has more than one owner and no ownership shares, so choose whose it is";
      }
      return owners
        ? "Paid online by the owner, not in the books yet: the books refused it"
        : "Paid online, not in the books yet: the lease is on a payment hold, or the books refused it";
    case "return":
      return owners
        ? "The owner's payment came back, not taken back out yet: their account holds too little"
        : "Came back, not undone yet: the owner's account holds too little";
    case "partner fee":
      return `The processor kept ${money(item.company_owes)}; the company's fee account holds ${money(item.company_holds)}`;
    case "fee refund":
      return `The tenant's ${money(item.company_owes)} fee came back; the company's fee account holds ${money(item.company_holds)}`;
    case "no word":
      return `Started online ${usDate(item.started_on ?? null)}, and the payments partner hasn't said since whether the bank paid:`
        + " look it up there. Its report brings it into the books; don't record it by hand.";
  }
}

// Owners pay in online (src/pms/api/owner_payments.py): into one of their property accounts, the
// processing fee always the company's.
export type OwnerAccount = {
  account_id: string;
  property: string;
  bank: string;
  balance: string;
  reserve: string;
  // What brings the account back up to its reserve: "0.00" when it holds that much.
  short: string;
};
export type OwnerPayment = { id: string; started_at: string; property: string; amount: string; status: OnlineStatus };
export type OwnerPayView = {
  available: boolean;
  why_not: WhyNot | null;
  how_to_pay: string | null;
  accounts: OwnerAccount[];
  payments: OwnerPayment[];
};

// Autopay (src/pms/api/autopay.py): a lease's rent paid each month from a bank account the tenant
// saved at the payments partner.
export type AutopayRule = "fixed" | "balance";
export type AutopayRun = {
  due_on: string;
  amount: string | null;
  tenant_fee: string | null;
  status: OnlineStatus | "skipped";
  skipped: string | null;
};
export type Autopay = {
  id: string;
  tenant: string;
  rule: AutopayRule;
  amount: string;
  account: string | null;
  started_at: string;
  stopped_at: string | null;
  stopped: string | null;
  runs: AutopayRun[];
};
export type AutopayView = {
  available: boolean;
  why_not: WhyNot | null;
  fee_payer: FeePayer;
  next_due: string | null;
  autopays: Autopay[];
};

// Why an autopay is off, in words.
const AUTOPAY_STOPPED: Record<string, string> = {
  tenant: "Turned off by the tenant",
  staff: "Turned off by the manager",
  "lease ended": "Stopped: the lease ended",
  "tenant left": "Stopped: the tenant left the lease",
  "failed twice": "Stopped: two payments in a row didn't go through",
  replaced: "Replaced by a newer autopay",
};

// What an autopay pays: "your balance due, up to $1,500.00" or "$1,200.00".
export function autopayRule(autopay: Pick<Autopay, "rule" | "amount">): string {
  return autopay.rule === "balance" ? `the balance due, up to ${money(autopay.amount)}` : money(autopay.amount);
}

// "On: pays the balance due, up to $1,500.00, on each rent due day, from Test bank ••••0000", or
// that it waits for its account, or why it is off.
export function autopayLine(autopay: Autopay): string {
  if (autopay.stopped) return AUTOPAY_STOPPED[autopay.stopped] ?? `Off: ${autopay.stopped}`;
  if (autopay.account === null) return `Waiting for a bank account to be saved: pays ${autopayRule(autopay)}`;
  const pays = autopayRule(autopay) + (autopay.rule === "balance" ? "," : "");
  return `On: pays ${pays} on each rent due day, from ${autopay.account}`;
}

// A due date autopay came to: "11/01/2026 · $1,200.00" with its status, or why it paid nothing.
export function autopayRunLine(run: AutopayRun): string {
  if (run.status === "skipped") return `${usDate(run.due_on)} · Nothing taken: ${run.skipped}`;
  const fee = run.tenant_fee && run.tenant_fee !== "0.00" ? ` + ${money(run.tenant_fee)} fee` : "";
  return `${usDate(run.due_on)} · ${money(run.amount ?? "0.00")}${fee}`;
}
