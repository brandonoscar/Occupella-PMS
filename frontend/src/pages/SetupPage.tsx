import type { Me } from "../api";
import { Card, PageHeader, Shell } from "../components/Shell";
import { STAFF_NAV } from "./staffNav";

// Settings → Company setup (ADR 0006, step 12): the company itself. What used to be set up here
// is on the page where it is used: bank accounts on Banking, owners on Owners, vendors on Vendors,
// properties, units, leases in place, management terms and fee terms on Properties and each
// property's page (terms on the owner's page too), and expense accounts in Chart of accounts.
export function SetupPage({ me }: { me: Me }) {
  return (
    <Shell me={me} nav={STAFF_NAV}>
      <PageHeader title="Company setup" />
      <Card title="Company">
        <dl className="text-base">
          <dt className="text-sm text-ink-muted">Name</dt>
          <dd>{me.pmc_name}</dd>
        </dl>
      </Card>
    </Shell>
  );
}
