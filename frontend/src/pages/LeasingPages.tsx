import type { Me } from "../api";
import { PageHeader, Shell } from "../components/Shell";
import { OffersCard } from "./OffersCard";
import { STAFF_NAV } from "./staffNav";

// Leasing → Draft leases: lease offers and renewals, sent to sign and made leases. An approved
// application's Create draft lease opens it on that application (?application=<id>).
export function DraftLeasesPage({ me }: { me: Me }) {
  const application = new URLSearchParams(window.location.search).get("application") ?? "";
  return (
    <Shell me={me} nav={STAFF_NAV}>
      <PageHeader title="Draft leases" />
      <OffersCard application={application} />
    </Shell>
  );
}
