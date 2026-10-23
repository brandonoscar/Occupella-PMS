import { useEffect, useState } from "react";
import { api } from "./api";
import { latest } from "./latest";
import { usDate } from "./money";

// State and local rules from a property's address (migration 20261023000202,
// src/pms/api/jurisdictions.py; ADR 0009, q142). A rule is cited information read in a statute's
// text on the day shown, which the company confirms; unknown is never "no rule". The checks put
// the rule beside the field it limits and warn when what staff typed goes past it: a warning,
// never a refusal.

export type RuleKind =
  | "deposit_cap"
  | "deposit_return_days"
  | "late_fee_cap"
  | "late_fee_grace_days"
  | "nsf_fee_cap"
  | "rent_increase_notice_days"
  | "rent_cap";

export type Shown = {
  rule: RuleKind;
  label: string;
  known: boolean;
  // "At most 1 month's rent", or why it is unknown.
  words: string;
  needs: string[];
  here: string | null;
  rule_id: string | null;
  level: string | null;
  jurisdiction_name: string | null;
  citation: string | null;
  url: string | null;
  checked_on: string | null;
  reviewed_by: string | null;
  exceptions: string | null;
  effective_from: string | null;
  effective_to: string | null;
  source: "pms" | "staff" | null;
  confirmed_by: string | null;
  confirmed_at: string | null;
};

export type LawWarning = { rule: RuleKind; said: string };

export type Checked = { rules: Shown[]; warnings: LawWarning[]; note: string };

export type Jurisdiction = {
  state_fips: string;
  state_name: string;
  county_fips: string | null;
  county_name: string | null;
  cousub_fips: string | null;
  cousub_name: string | null;
  place_fips: string | null;
  place_name: string | null;
  tract: string | null;
  source: "census" | "fake" | "staff";
  recorded_by: string | null;
  recorded_at: string;
};

export type Facts = {
  year_built: number | null;
  units_in_building: number | null;
  owner_occupied: boolean | null;
  owner_kind: "person" | "company" | null;
  recorded_by: string | null;
  recorded_at: string | null;
};

export type Level = { level: "federal" | "state" | "county" | "city"; jurisdiction: string; name: string };

export type Law = {
  day: string;
  jurisdiction: Jurisdiction | null;
  has_address: boolean;
  facts: Facts;
  levels: Level[];
  rules: Shown[];
  note: string;
};

// Where a property is in the law, in words: "Demo City city, Demo County, California".
export function placeLine(where: Jurisdiction): string {
  const parts = [where.place_name ?? "outside any city", where.county_name, where.state_name];
  return parts.filter((p): p is string => p !== null).join(", ");
}

// Who said where it is.
export function placeSource(where: Jurisdiction): string {
  if (where.source === "staff") return `Confirmed by ${where.recorded_by ?? "staff"} on ${usDate(where.recorded_at.slice(0, 10))}`;
  const by = where.source === "census" ? "the Census geocoder" : "the stand-in geocoder";
  return `Found by ${by} on ${usDate(where.recorded_at.slice(0, 10))}`;
}

// A rule's source, read day and confirmation in words.
export function citedLine(rule: Shown): string {
  if (!rule.known || rule.citation === null) return "";
  const read = rule.checked_on ? `, read ${usDate(rule.checked_on)} by ${rule.reviewed_by ?? "someone"}` : "";
  return `${rule.jurisdiction_name}: ${rule.citation}${read}`;
}

export function confirmedLine(rule: Shown): string {
  if (!rule.known) return "";
  if (rule.confirmed_by === null) return "Not confirmed by your company yet";
  return `Confirmed by ${rule.confirmed_by}`;
}

const FACT_WORDS: Record<string, string> = {
  year_built: "Year built",
  units_in_building: "Units in the building",
  owner_occupied: "An owner lives there",
  owner_kind: "The owner is",
};

// The facts on file, each "not known" until staff say.
export function factLines(facts: Facts): [string, string][] {
  const yes = (v: boolean | null) => (v === null ? "Not known" : v ? "Yes" : "No");
  return [
    [FACT_WORDS.year_built!, facts.year_built === null ? "Not known" : String(facts.year_built)],
    [FACT_WORDS.units_in_building!, facts.units_in_building === null ? "Not known" : String(facts.units_in_building)],
    [FACT_WORDS.owner_occupied!, yes(facts.owner_occupied)],
    [FACT_WORDS.owner_kind!, facts.owner_kind === null ? "Not known" : facts.owner_kind === "person" ? "A person" : "A company"],
  ];
}

// A check's query from what a form holds, leaving out what is empty.
export function checkQuery(params: Record<string, string | null | undefined>): string {
  const query = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value !== null && value !== undefined && value.trim() !== "") query.set(key, value.trim());
  }
  return query.toString();
}

function isChecked(answer: unknown): answer is Checked {
  return typeof answer === "object" && answer !== null && Array.isArray((answer as Checked).warnings);
}

// The answer to a check, asked again a moment after what it checks stops changing (a form being
// typed in) or `again` does (a record refreshed after a save); null while there is nothing to
// check or the check was refused (a number half typed).
export function useLawCheck(path: string | null, again?: unknown): Checked | null {
  const [found, setFound] = useState<Checked | null>(null);
  useEffect(() => {
    const run = latest();
    if (path === null) {
      setFound(null);
      return run.stop;
    }
    const timer = window.setTimeout(() => {
      api.get<unknown>(path)
        .then(run.keep((answer: unknown) => setFound(isChecked(answer) ? answer : null)))
        .catch(run.keep(() => setFound(null)));
    }, 250);
    return () => {
      window.clearTimeout(timer);
      run.stop();
    };
  }, [path, again]);
  return found;
}
