// Everyone who may sign in, as Settings, Logins lists them (src/pms/api/people.py).
import type { BadgeTone } from "./components/Badge";
import type { Portal } from "./rentals";

export type Person = {
  kind: "staff" | "owner" | "tenant";
  person_id: string;
  name: string;
  email: string | null;
  login_id: string | null;
  status: Portal;
};

export const ROLE: Record<Person["kind"], string> = { staff: "Staff", owner: "Owner", tenant: "Tenant" };

// Where a person's own page is: an owner's or tenant's, or the staff page for staff.
export function personHref(person: Person): string {
  return person.kind === "owner" ? `/owners/${person.person_id}`
    : person.kind === "tenant" ? `/tenants/${person.person_id}`
    : "/settings/staff";
}

// A login's state as a badge: signed in is fine, an invite out is news, none yet is neutral, and
// a disabled one is for good.
export const LOGIN: Record<Portal, { label: string; tone: BadgeTone }> = {
  "no login": { label: "No login", tone: "neutral" },
  invited: { label: "Invited", tone: "info" },
  active: { label: "Active", tone: "positive" },
  disabled: { label: "Disabled", tone: "caution" },
};
