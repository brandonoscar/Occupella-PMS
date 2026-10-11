import type { Me } from "../api";
import { Notice } from "../components/Notice";
import { Card, Shell } from "../components/Shell";

// The tenant portal (lease, ledger, balance due, maintenance requests) is the next PR; until
// then a tenant who signs in sees who they are and nothing that could be read as a balance.
export function TenantHome({ me }: { me: Me }) {
  return (
    <Shell me={me}>
      <h1 className="font-display text-xl font-medium">Welcome, {me.name}</h1>
      <Card title="Your lease">
        <Notice tone="info">
          Your lease, charges and payments will show here soon. Ask {me.pmc_name} for your
          balance until then.
        </Notice>
      </Card>
    </Shell>
  );
}
