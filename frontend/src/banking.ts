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
