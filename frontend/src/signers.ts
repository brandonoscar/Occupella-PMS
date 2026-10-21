// Who may pay money out of each trust bank account, as the API sends it (src/pms/api/signers.py),
// and how it reads.
import type { BadgeTone } from "./components/Badge";
import { money, usDate } from "./money";

export type Signer = {
  staff_id: string;
  name: string;
  role: "broker" | "signer";
  license_number: string | null;
  bond_amount: string | null;
  bond_expires_on: string | null;
  authorized_on: string;
  // licensed, bonded, "bond runs out in N days" or "bond ran out".
  standing: string;
};
export type SignerAccount = {
  bank_account_id: string;
  bank_account: string;
  // The broker first, then the signers; none: no broker on file.
  signers: Signer[];
};

// "Broker 1, the broker: license LIC-0001, authorized 2026-01-01", or a bonded signer's bond.
export function signerLine(signer: Signer): string {
  const who = signer.role === "broker" ? `${signer.name}, the broker` : signer.name;
  const how =
    signer.license_number !== null
      ? `license ${signer.license_number}`
      : `bonded for ${money(signer.bond_amount)} through ${usDate(signer.bond_expires_on)}`;
  return `${who}: ${how}, authorized ${usDate(signer.authorized_on)}`;
}

export function standingTone(standing: string): BadgeTone {
  if (standing === "bond ran out") return "danger";
  if (standing.startsWith("bond runs out")) return "caution";
  return "neutral";
}
