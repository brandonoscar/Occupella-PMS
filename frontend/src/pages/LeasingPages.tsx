import type { Me } from "../api";
import { PageHeader, Shell } from "../components/Shell";
import { LeasingCard } from "./LeasingCard";
import { OffersCard } from "./OffersCard";
import { STAFF_NAV } from "./staffNav";

// Leasing → Listings: each unit's application link, listed on the public vacancy page or not.
export function RentalListingsPage({ me }: { me: Me }) {
  return (
    <Shell me={me} nav={STAFF_NAV}>
      <PageHeader title="Listings" />
      <LeasingCard pmcId={me.pmc_id} part="links" />
    </Shell>
  );
}

// Leasing → Applicants: the applications that came through the links, their screenings and
// decisions, and the adverse action notices given.
export function ApplicantsPage({ me }: { me: Me }) {
  return (
    <Shell me={me} nav={STAFF_NAV}>
      <PageHeader title="Applicants" />
      <LeasingCard part="applications" />
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

