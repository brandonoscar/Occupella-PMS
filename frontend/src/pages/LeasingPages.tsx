import type { Me } from "../api";
import { PageHeader, Shell } from "../components/Shell";
import { LeasingCard } from "./LeasingCard";
import { MaintenanceCard } from "./MaintenanceCard";
import { OffersCard } from "./OffersCard";
import { STAFF_NAV } from "./staffNav";

// Leasing → Applicants: application links (and listing them on the vacancy page), applications,
// screenings and decisions.
export function ApplicantsPage({ me }: { me: Me }) {
  return (
    <Shell me={me} nav={STAFF_NAV}>
      <PageHeader title="Applicants" />
      <LeasingCard pmcId={me.pmc_id} />
    </Shell>
  );
}

// Leasing → Draft leases: lease offers and renewals, sent to sign and made leases.
export function DraftLeasesPage({ me }: { me: Me }) {
  return (
    <Shell me={me} nav={STAFF_NAV}>
      <PageHeader title="Draft leases" />
      <OffersCard />
    </Shell>
  );
}

// Maintenance → Work orders: tenants' requests and the work orders that answer them.
export function WorkOrdersPage({ me }: { me: Me }) {
  return (
    <Shell me={me} nav={STAFF_NAV}>
      <PageHeader title="Work orders" />
      <MaintenanceCard />
    </Shell>
  );
}
