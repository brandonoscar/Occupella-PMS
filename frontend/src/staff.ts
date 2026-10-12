// The PMC's staff and their roles as the API sends them (src/pms/api/staff.py).
import type { Me, Role } from "./api";

export type Member = { staff_id: string; name: string; roles: Role[] };

// Each role, in the order the page shows them, and what it lets a staff member do.
export const ROLES: { role: Role; label: string; does: string }[] = [
  { role: "admin", label: "Admin", does: "changes roles, invites and disables staff logins" },
  { role: "approve", label: "Approve", does: "approves month-end, signs off a cutover" },
  { role: "disburse", label: "Pay out", does: "pays bills and owner draws" },
];

export function holds(me: Me, role: Role): boolean {
  return me.roles?.includes(role) ?? false;
}

// What a role change does, said after it is recorded.
export function changeLabel(member: Member, role: Role, granted: boolean): string {
  const label = ROLES.find((r) => r.role === role)?.label ?? role;
  return granted ? `${member.name} may now: ${label}.` : `${member.name} may no longer: ${label}.`;
}
