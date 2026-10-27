import { money, usDate } from "./money";
import type { PayoutMethod } from "./payables";

// Paying owners and vendors (ADR 0010 phase 1b; src/pms/api/payouts.py): the draw day, how each
// owner is paid, payees' bank accounts (only their last four ever come back), what a trust bank
// account's bank asks in an ACH file, the draws prepared on the draw day, and the files of
// payouts recorded together: an ACH file for the bank and checks to print.

export type DrawDay = { draw_day: number | null; changed_at: string | null; changed_by: string | null };
export const DRAW_DAY = "/api/payouts/draw-day";

export type BankShown = {
  routing_number: string;
  account_type: "checking" | "savings";
  last4: string;
  holder_name: string;
  recorded_at: string;
  recorded_by: string | null;
};
export type Payee = { method: PayoutMethod | null; bank: BankShown | null };

export type BankSettings = {
  bank_account_id: string;
  bank_routing_number: string | null;
  bank_name: string | null;
  origin_id: string | null;
  origin_name: string | null;
  company_id: string | null;
  company_name: string | null;
  first_check_number: number | null;
  ach_ready: boolean;
  next_check_number: number;
  changed_at: string | null;
  changed_by: string | null;
};

export type DrawLine = {
  account: string;
  owner_id: string;
  owner: string;
  property: string;
  amount: string;
  available_now: string;
  method: PayoutMethod;
};
export type DrawRun = { id: string; month: string; prepared_on: string; lines: DrawLine[]; total: string };

export type PayoutRun = {
  id: string;
  kind: "owners" | "bills";
  method: PayoutMethod;
  bank_account_id: string;
  bank_account: string;
  paid_on: string;
  payouts: number;
  total: string;
  recorded_by: string | null;
  recorded_at: string;
  downloads: number;
};

// How a payout leaves, as staff read it: ACH, not EFT.
export function methodWord(method: PayoutMethod): string {
  return method === "EFT" ? "ACH" : "Check";
}

// "Checking ····6789, routing 001234561, Owner 1": a bank account as it shows, never whole.
export function bankLine(bank: BankShown): string {
  const kind = bank.account_type === "savings" ? "Savings" : "Checking";
  return `${kind} ····${bank.last4}, routing ${bank.routing_number}, ${bank.holder_name}`;
}

// "Prepared 10/15/2026 for 3 owner accounts, $1,200.00": the draws waiting on the draw day.
export function drawRunLine(run: DrawRun): string {
  const n = run.lines.length;
  return `Prepared ${usDate(run.prepared_on)} for ${n} owner account${n === 1 ? "" : "s"}, ${money(run.total)}.`;
}

// Where a run's file downloads from.
export function runFile(run: Pick<PayoutRun, "id" | "method">): { href: string; label: string } {
  return run.method === "EFT"
    ? { href: `/api/payouts/runs/${run.id}/ach`, label: "Download the ACH file" }
    : { href: `/api/payouts/runs/${run.id}/checks`, label: "Print the checks" };
}
