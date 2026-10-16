import type { Me } from "../api";
import { LinkButton } from "../components/Button";
import { Card, PageHeader, Shell } from "../components/Shell";
import { staffDownloads } from "../reports";
import { STAFF_NAV } from "./staffNav";

// Every report as of today, in each format, and the whole ledger (ADR 0005: the downloads that
// were on Overview).
export function ReportsPage({ me }: { me: Me }) {
  return (
    <Shell me={me} nav={STAFF_NAV}>
      <PageHeader title="Reports" />
      <Card title="Downloads">
        <div className="flex flex-wrap gap-2">
          {staffDownloads(new Date()).map((download) => (
            <LinkButton key={download.label} href={download.href}>
              {download.label}
            </LinkButton>
          ))}
        </div>
      </Card>
    </Shell>
  );
}
