// Balance notices as the API sends them (src/pms/api/balance_notices.py, migration
// 20261023000201): the leases that owe, and what a send did.
export type Delivery = {
  state: string | null;
  // Whether email may serve the legal notice for nonpayment there at all (with written consent).
  email_may_count: boolean | null;
  words: string;
};

export type Tenant = { id: string; name: string; email: string | null };

export type Owing = {
  lease_id: string;
  property: string;
  unit: string;
  tenants: Tenant[];
  owed: string;
  delivery: Delivery;
};

export type Sent = {
  lease_id: string;
  notice_id: string;
  property: string;
  unit: string;
  owed: string;
  emailed: Tenant[];
  without_email: string[];
  // Tenants texted a reminder of it (who agreed to texts, when staff asked).
  texted?: string[];
  delivery: Delivery;
};

export type Run = { id: string; sent: Sent[] };

// "Emailed 3 tenants about 2 leases; 1 tenant has no email on file."
export function sentLine(run: Run): string {
  const emailed = run.sent.reduce((n, s) => n + s.emailed.length, 0);
  const without = run.sent.reduce((n, s) => n + s.without_email.length, 0);
  const people = (n: number) => `${n} tenant${n === 1 ? "" : "s"}`;
  const leases = `${run.sent.length} lease${run.sent.length === 1 ? "" : "s"}`;
  const missing = without === 0 ? "" : `; ${people(without)} ${without === 1 ? "has" : "have"} no email on file`;
  const texted = run.sent.reduce((n, s) => n + (s.texted?.length ?? 0), 0);
  const texts = texted === 0 ? "" : `; texted ${people(texted)} a reminder`;
  return `Made ${run.sent.length === 1 ? "a balance notice" : "balance notices"} for ${leases}: emailed ${people(emailed)}${missing}${texts}.`;
}

// The states the leases are in whose legal notice must go by mail, posting or hand, once each.
export function paperStates(rows: { delivery: Delivery }[]): string[] {
  return [...new Set(rows.filter((r) => r.delivery.email_may_count === false).map((r) => r.delivery.state ?? ""))]
    .filter(Boolean)
    .sort();
}
