import type { Me } from "../api";
import { LinkButton } from "../components/Button";
import { Card, PageHeader, Shell } from "../components/Shell";
import { GoLink } from "./NeedsAttention";
import { STAFF_NAV } from "./staffNav";

const LINK = "font-medium text-iris hover:text-iris-hover hover:underline";

// Settings → Data export (ADR 0006): the company's whole ledger as a zip of CSV files, every
// trust bank account, ledger account, transfer and entry (src/pms/exports.py); and everything
// the company keeps (q132): every record as CSV, its documents, leases and signatures as files,
// and the ledger, for an admin, since it holds everyone's contact details and files.
export function DataExportPage({ me }: { me: Me }) {
  return (
    <Shell me={me} nav={STAFF_NAV}>
      <PageHeader title="Data export" />
      <p className="text-base text-ink-muted"><GoLink href="/settings" className={LINK}>Settings</GoLink></p>
      <Card title="The ledger">
        <p className="mb-3 text-base text-ink-muted">
          Every trust bank account, ledger account, transfer and entry, as CSV files in a zip.
        </p>
        <LinkButton href="/api/ledger.zip">Whole ledger (CSV, zip)</LinkButton>
      </Card>
      <Card title="Everything">
        <p className="mb-3 text-base text-ink-muted">
          Every record the company keeps (properties, leases, charges and payments, bills, work
          orders, contact details, notes, histories and the audit log) as CSV files, its documents,
          leases and signatures as files, and the ledger, in one zip. It holds everyone&apos;s
          contact details and files, so only an admin can download it. Passwords, sessions and
          links&apos; secrets are never in it.
        </p>
        <LinkButton href="/api/records.zip">Everything (CSV and files, zip)</LinkButton>
      </Card>
    </Shell>
  );
}
