// Rental applications as the API sends them (src/pms/api/leasing.py), and how they read.
import type { BadgeTone } from "./components/Badge";
import type { UnitDetails } from "./unitDetails";

export type Listing = {
  pmc: string;
  property: string;
  unit: string;
  monthly_rent: string;
  // The company's screening criteria in words, the same for every applicant (migration 0089).
  criteria?: string | null;
};

// A unit on a PMC's public vacancy page, and the page.
export type Vacancy = {
  id: string;
  property: string;
  unit: string;
  monthly_rent: string;
  description: string | null;
  listed_at: string;
  // The unit's listing photos, oldest first (migration 0087).
  photos: string[];
  // What the unit has (migration 0124); its market rent stays with staff.
  bedrooms: number | null;
  bathrooms: string | null;
  square_feet: number | null;
};
export type Vacancies = { pmc: string; listings: Vacancy[] };

// What an applicant applies through: a listing from the public page (/apply?listing=<id>), else
// the token of a link staff posted (/apply#<token>).
export type ApplyVia = { listing: string } | { token: string };
export function applyVia(search: string, hash: string): ApplyVia {
  const listing = new URLSearchParams(search).get("listing");
  return listing ? { listing } : { token: hash.replace(/^#/, "") };
}

export type ApplicantStatus = {
  property: string;
  unit: string;
  submitted_at: string;
  status: "received" | "in review" | "approved" | "conditional" | "declined" | "withdrawn";
  conditions: string | null;
  // The adverse action notice of a decision that rests on a screening (q080).
  notice?: string | null;
};

export type Applicant = { name: string; email: string };

export type Screening = {
  id: string;
  partner: string;
  reference: string;
  recommendation: "approve" | "conditional" | "decline";
  reasons: string[];
  reported_at: string;
};

export type AdverseAction = { reasons: string[]; notice_given_on: string };

export type Decision = {
  decision: "approved" | "conditional" | "declined";
  conditions: string | null;
  screening_id: string | null;
  decided_at: string;
  adverse_action: AdverseAction | null;
  // Why the decision went against the latest screening's recommendation (migration 0089).
  override_reason?: string | null;
};

// The decision that goes with each recommendation; any other goes against it and says why.
const AGREES: Record<string, Decision["decision"]> = { approve: "approved", conditional: "conditional", decline: "declined" };

// Whether deciding this goes against the application's latest screening.
export function againstScreening(application: { screenings: { recommendation: string }[] }, decision: Decision["decision"]): boolean {
  const latest = application.screenings.at(-1);
  return latest !== undefined && AGREES[latest.recommendation] !== decision;
}

// Where an application stands for staff: new until screened, screening until decided, then the
// decision (a decline reads as rejected), or withdrawn.
export type ApplicationStatus = "new" | "screening" | "approved" | "conditional" | "rejected" | "withdrawn";

export type Withdrawal = { reason: string | null; recorded_at: string; recorded_by: string | null };

export type Application = {
  id: string;
  property_id: string;
  property: string;
  unit_id: string;
  unit: string;
  monthly_rent: string;
  move_in_on: string;
  monthly_income: string;
  submitted_at: string;
  applicants: Applicant[];
  screenings: Screening[];
  decision: Decision | null;
  status: ApplicationStatus;
  withdrawal: Withdrawal | null;
  // The lease made of it, once an offer of it became one.
  lease_id: string | null;
};

// An application's status as staff read it, in a badge.
export function applicationBadge(status: ApplicationStatus): { word: string; tone: BadgeTone } {
  return {
    new: { word: "New", tone: "info" as const },
    screening: { word: "Screening", tone: "caution" as const },
    approved: { word: "Approved", tone: "positive" as const },
    conditional: { word: "Approved on conditions", tone: "positive" as const },
    rejected: { word: "Rejected", tone: "danger" as const },
    withdrawn: { word: "Withdrawn", tone: "neutral" as const },
  }[status];
}

// Its applicants' names, as one line.
export function applicantNames(application: Application): string {
  return application.applicants.map((a) => a.name).join(", ");
}

// What its latest screening recommends, or "" before any.
export function latestScreening(application: Application): string {
  const last = application.screenings.at(-1);
  return last ? recommendationLabel(last.recommendation) : "";
}

// A unit on Leasing, Listings: coming free within 90 days, or with an open application link.
export type OpenUnit = {
  unit_id: string;
  // Its bedrooms, bathrooms, square feet and market rent (migration 0124).
  details: UnitDetails;
  property_id: string;
  property: string;
  unit: string;
  // The first day from today no lease holds it, if within 90 days.
  available_on: string | null;
  // The rent its latest lease charges or charged last; null if never leased.
  last_rent: string | null;
  // Its open application link (a listed one first), the rent it asks and its applications.
  link_id: string | null;
  link_rent: string | null;
  applications: number;
  // The link's listing on the public page, while it is there, since when and for how many days.
  listing_id: string | null;
  description: string | null;
  listed_at: string | null;
  days_listed: number | null;
  // How many listing photos the unit has (migration 0087).
  photos: number;
  // Whether its listing goes to listing sites through the feeds (migration 0250), and what the
  // sites ask that it lacks, in words.
  in_feed: boolean;
  feed_needs: string[];
};

// Where a unit stands with the listing sites: sent; sent but held back from the feeds while it
// lacks what the sites ask (its details cleared since); or not sent.
export function feedStanding(unit: OpenUnit): { tone: "positive" | "neutral" | "caution"; label: string } {
  if (unit.in_feed) {
    return unit.feed_needs.length > 0 ? { tone: "caution", label: "Held back" } : { tone: "positive", label: "Sent" };
  }
  return { tone: "neutral", label: "Not sent" };
}

// Settings → Listing feeds (migration 0250): each feed's URL for its site, the leasing contact
// the listings name, what the company still needs and how many listings they carry.
export type FeedSettings = {
  contact: { contact_name: string | null; email: string; phone: string } | null;
  feeds: { site: "zillow" | "mits"; takes: string; url: string }[];
  missing: string[];
  listings: number;
};

// A phone of ten digits as it reads: (202) 555-0142.
export function feedPhone(digits: string): string {
  return /^\d{10}$/.test(digits) ? `(${digits.slice(0, 3)}) ${digits.slice(3, 6)}-${digits.slice(6)}` : digits;
}

// A unit's listing photo, as staff keep them.
export type ListingPhoto = { id: string; title: string; uploaded_at: string };

// What an applicant reads about their application.
export function applicantStatus(status: ApplicantStatus["status"]): string {
  return {
    received: "Received. The property manager hasn't looked at it yet.",
    "in review": "In review.",
    approved: "Approved.",
    conditional: "Approved, on conditions:",
    declined: "Declined. The property manager will tell you why.",
    withdrawn: "Withdrawn.",
  }[status];
}

// What a screening recommends, for staff.
export function recommendationLabel(recommendation: Screening["recommendation"]): string {
  return { approve: "Recommends approval", conditional: "Recommends conditions", decline: "Recommends declining" }[
    recommendation
  ];
}

// Whether a decision needs an adverse action notice: a decline or a conditional approval that
// rests on a screening (the database checks the same at commit).
export function needsNotice(decision: Decision["decision"], screeningId: string | null): boolean {
  return decision !== "approved" && screeningId !== null;
}

// Monthly income as a multiple of the rent, to one decimal ("3.0x"), or "" when there's no rent.
export function incomeMultiple(income: string, rent: string): string {
  const ratio = Number(income) / Number(rent);
  return Number.isFinite(ratio) && Number(rent) > 0 ? `${(Math.floor(ratio * 10) / 10).toFixed(1)}x` : "";
}
