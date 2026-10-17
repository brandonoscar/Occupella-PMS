import type { Me } from "../api";
import { LinkButton } from "../components/Button";
import { PageHeader, Shell } from "../components/Shell";
import { GoLink } from "./NeedsAttention";
import { STAFF_NAV } from "./staffNav";

const LINK = "font-medium text-iris hover:text-iris-hover hover:underline";

// Settings → Data export (ADR 0006): the company's whole ledger as a zip of CSV files, every
// trust bank account, ledger account, transfer and entry (src/pms/exports.py).
export function DataExportPage({ me }: { me: Me }) {
  return (
    <Shell me={me} nav={STAFF_NAV}>
      <PageHeader title="Data export" actions={<LinkButton href="/api/ledger.zip">Whole ledger (CSV, zip)</LinkButton>} />
      <p className="text-base text-ink-muted"><GoLink href="/settings" className={LINK}>Settings</GoLink></p>
    </Shell>
  );
}
