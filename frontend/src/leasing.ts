// Rental applications as the API sends them (src/pms/api/leasing.py), and how they read.
import type { BadgeTone } from "./components/Badge";

export type Listing = { pmc: string; property: string; unit: string; monthly_rent: string };

// A unit on a PMC's public vacancy page, and the page.
export type Vacancy = {
  id: string;
  property: string;
  unit: string;
  monthly_rent: string;
  description: string | null;
  listed_at: string;
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
};

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
  // The link's listing on the public page, while it is there.
  listing_id: string | null;
  description: string | null;
};

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
