// Rent paid online (migration 0158, the founder 2026-10-23): a tenant pays by card or bank debit
// through the server's payment processor, on its own page; the PMS records the payment once the
// processor says the money came. The processor bills its fees to the company.
export type OnlineMethod = "card" | "bank debit";
export type OnlineStatus = "started" | "succeeded" | "failed" | "refused" | "returned";

export type OnlinePayment = {
  id: string;
  lease_id: string;
  home: string;
  tenant: string;
  amount: string;
  method: OnlineMethod;
  started_at: string;
  status: OnlineStatus;
  reason: string | null;
};

export type OnlineSettings = {
  cards: boolean;
  bank_debits: boolean;
  recorded_by: string | null;
  recorded_at: string | null;
};

export const METHOD_WORDS: Record<OnlineMethod, string> = { card: "Card", "bank debit": "Bank account (ACH)" };

// How a payment stands, as a badge says it.
export const STATUS: Record<OnlineStatus, { label: string; tone: "info" | "positive" | "danger" | "neutral" }> = {
  started: { label: "Processing", tone: "info" },
  succeeded: { label: "Paid", tone: "positive" },
  failed: { label: "Didn't go through", tone: "danger" },
  refused: { label: "Refused: being refunded", tone: "danger" },
  returned: { label: "Came back", tone: "danger" },
};

// "Cards and bank debits", "Cards only", "Bank debits only", "Not taken".
export function takenLine(s: Pick<OnlineSettings, "cards" | "bank_debits">): string {
  if (s.cards && s.bank_debits) return "Cards and bank debits";
  if (s.cards) return "Cards only";
  if (s.bank_debits) return "Bank debits only";
  return "Not taken";
}
