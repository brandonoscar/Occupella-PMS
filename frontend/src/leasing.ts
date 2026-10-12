// Rental applications as the API sends them (src/pms/api/leasing.py), and how they read.

export type Listing = { pmc: string; property: string; unit: string; monthly_rent: string };

export type ApplicantStatus = {
  property: string;
  unit: string;
  submitted_at: string;
  status: "received" | "in review" | "approved" | "conditional" | "declined";
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

export type Application = {
  id: string;
  property: string;
  unit: string;
  monthly_rent: string;
  move_in_on: string;
  monthly_income: string;
  submitted_at: string;
  applicants: Applicant[];
  screenings: Screening[];
  decision: Decision | null;
};

// What an applicant reads about their application.
export function applicantStatus(status: ApplicantStatus["status"]): string {
  return {
    received: "Received. The property manager hasn't looked at it yet.",
    "in review": "In review.",
    approved: "Approved.",
    conditional: "Approved, on conditions:",
    declined: "Declined. The property manager will tell you why.",
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
