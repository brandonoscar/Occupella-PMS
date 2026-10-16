// The general ledger as the API sends it (src/pms/api/general_ledger.py), and its downloads.
export type LedgerEntry = {
  posted_at: string;
  description: string;
  debit: string;
  credit: string;
  balance: string;
};
export type LedgerAccount = {
  bank: string;
  account: string;
  prior_balance: string;
  entries: LedgerEntry[];
  debits: string;
  credits: string;
  balance: string;
};

// This month so far, as the page opens: the first of the month (UTC) through today.
export function monthToDate(today: string): { from: string; to: string } {
  return { from: `${today.slice(0, 8)}01`, to: today };
}

// The same days as trust_report_general_ledger's download.
export function ledgerHref(from: string, to: string, format: "pdf" | "csv" | "xlsx"): string {
  return `/api/reports/trust_report_general_ledger?${new URLSearchParams([
    ["arg", from],
    ["arg", to],
    ["format", format],
  ])}`;
}

// An amount that is no amount ("0.00" debit or credit) shows as nothing, as a ledger prints it.
export function nonZero(amount: string): string | null {
  return Number(amount) === 0 ? null : amount;
}
