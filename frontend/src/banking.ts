// A trust bank account's register and breakdown as the API sends them (src/pms/api/banking.py).
import type { OwnerMoney } from "./payables";

export type RegisterLine = {
  transfer_id: string;
  on: string;
  type: string;
  party: string | null;
  memo: string | null;
  property: string | null;
  deposit: string | null;
  withdrawal: string | null;
  balance: string;
};
export type Held = { kind: string; label: string; amount: string };
export type Breakdown = { book_cash: string; held: Held[]; owners: OwnerMoney[] };

// What came into a trust bank account and is in no bank deposit yet: each payment once.
export type Undeposited = {
  id: string;
  transfer_ids: string[];
  on: string;
  type: string;
  party: string | null;
  memo: string | null;
  amount: string;
};
// A bank deposit: what staff took to the bank together (src/pms/api/bank_deposits.py).
export type BankDeposit = {
  id: string;
  deposited_on: string;
  total: string;
  items: number;
  recorded_by: string | null;
  matched: boolean;
};
