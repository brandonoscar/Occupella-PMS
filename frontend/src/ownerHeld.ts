import { money, usDate } from "./money";
import { toCents } from "./reconcile";
import type { DepositKind } from "./rent";

// A tenant's deposit the owner holds, outside trust (migration 20261106000241,
// src/pms/api/owner_held.py): never in a trust balance and no money moves; at move-out the owner
// returns it, and staff record what the owner returned and kept.
export type OwnerHeldSettled = { returned: string; kept: string; on: string; memo: string | null };
export type OwnerHeld = {
  id: string;
  tenant_id: string;
  tenant: string;
  kind: DepositKind;
  amount: string;
  held_since: string;
  memo: string | null;
  settled: OwnerHeldSettled | null;
};

const KIND_WORD: Record<DepositKind, string> = { security: "security deposit", pet: "pet deposit", key: "key deposit" };

// "Morgan Demo's security deposit: $1,500.00, since 01/01/2026"
export function ownerHeldLine(held: OwnerHeld): string {
  return `${held.tenant}'s ${KIND_WORD[held.kind]}: ${money(held.amount)}, since ${usDate(held.held_since)}`;
}

// "The owner returned $1,200.00 and kept $300.00 on 06/30/2026."
export function settledLine(settled: OwnerHeldSettled): string {
  return `The owner returned ${money(settled.returned)} and kept ${money(settled.kept)} on ${usDate(settled.on)}.`;
}

// What the owner holds of a lease's deposits, not yet returned or kept, in cents.
export function stillHeldCents(held: OwnerHeld[]): number {
  return held.filter((h) => h.settled === null).reduce((sum, h) => sum + toCents(h.amount), 0);
}
