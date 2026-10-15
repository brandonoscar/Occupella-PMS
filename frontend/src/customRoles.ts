// Custom staff roles as the API sends them (src/pms/api/custom_roles.py): each role, the areas
// of the work it may change and who holds it. A staff member with custom roles changes only
// what their roles allow; one with none works everywhere.
export type AreaChoice = { area: string; label: string };
export type CustomRole = {
  id: string;
  name: string;
  areas: string[];
  staff: { staff_id: string; name: string }[];
};
export type CustomRoles = { areas: AreaChoice[]; roles: CustomRole[] };

// "Bookkeeping may change: bills, the journal." or "… may change nothing: look only."
export function roleSummary(role: CustomRole, choices: AreaChoice[]): string {
  const labels = choices.filter((c) => role.areas.includes(c.area)).map((c) => c.label);
  return labels.length
    ? `${role.name} may change: ${labels.join(", ")}.`
    : `${role.name} may change nothing: look only.`;
}

// The custom roles a staff member holds, by name.
export function rolesHeld(roles: CustomRole[], staffId: string): string[] {
  return roles.filter((r) => r.staff.some((s) => s.staff_id === staffId)).map((r) => r.name);
}
