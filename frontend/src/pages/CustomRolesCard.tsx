import { UsersRound } from "lucide-react";
import { useEffect, useState } from "react";
import { ApiError, api } from "../api";
import { Button } from "../components/Button";
import { EmptyState } from "../components/EmptyState";
import { Field } from "../components/Field";
import { Notice } from "../components/Notice";
import { Card } from "../components/Shell";
import type { CustomRole, CustomRoles } from "../customRoles";
import { roleSummary } from "../customRoles";
import type { Member } from "../staff";
import { useRecord } from "./useRecord";

// The company's custom roles: the areas each may change and who holds it. A staff member given
// custom roles changes only what their roles allow and still sees everything; one with none
// works everywhere. Only an admin changes them (the database refuses anyone else).
export function CustomRolesCard({ members, admin }: { members: Member[]; admin: boolean }) {
  const [roles, setRoles] = useState<CustomRoles | null>(null);
  const [error, setError] = useState("");
  const [name, setName] = useState("");
  const { said, error: refused, record } = useRecord(refresh);

  function refresh() {
    api
      .get<CustomRoles>("/api/custom-roles")
      .then(setRoles)
      .catch((failed) => setError(failed instanceof ApiError ? failed.detail : String(failed)));
  }
  useEffect(refresh, []);

  return (
    <Card title="Custom roles">
      <p className="mb-3 text-sm text-ink-muted">
        A staff member given custom roles changes only what their roles allow, and still sees
        every page. One with none works everywhere.
      </p>
      {roles === null ? (
        <Notice tone={error ? "error" : "info"}>{error || "Loading"}</Notice>
      ) : roles.roles.length === 0 ? (
        <EmptyState icon={UsersRound} title="No custom roles yet"
          body="Make one to limit what a staff member may change, such as a leasing agent's." />
      ) : (
        <ul className="flex flex-col divide-y divide-line">
          {roles.roles.map((role) => (
            <RoleRow key={role.id} role={role} roles={roles} members={members} admin={admin}
              change={record} />
          ))}
        </ul>
      )}
      <Notice tone="info">{said}</Notice>
      <Notice tone="error">{refused}</Notice>
      {admin && (
        <form
          className="mt-4 flex flex-wrap items-end gap-3"
          onSubmit={async (event) => {
            event.preventDefault();
            if (await record("/api/custom-roles", { name, areas: [] }, () =>
              `Made ${name}: choose what it may change, then who holds it.`)) {
              setName("");
            }
          }}
        >
          <Field label="New role" id="custom-role-name" required maxLength={100} value={name}
            onChange={(e) => setName(e.target.value)} />
          <Button type="submit">Make role</Button>
        </form>
      )}
    </Card>
  );
}

function RoleRow({ role, roles, members, admin, change }: {
  role: CustomRole;
  roles: CustomRoles;
  members: Member[];
  admin: boolean;
  change: ReturnType<typeof useRecord>["record"];
}) {
  const holds = (staffId: string) => role.staff.some((s) => s.staff_id === staffId);
  return (
    <li className="flex flex-col gap-3 py-4" aria-label={role.name}>
      <p>
        <span className="font-medium">{role.name}</span>
        <span className="text-ink-muted"> · {roleSummary(role, roles.areas)}</span>
      </p>
      <fieldset className="flex flex-wrap gap-x-4 gap-y-1 text-sm" aria-label={`What ${role.name} may change`}>
        {roles.areas.map((choice) => {
          const allowed = role.areas.includes(choice.area);
          return (
            <label key={choice.area} className="flex items-center gap-1.5">
              <input type="checkbox" checked={allowed} disabled={!admin}
                aria-label={`${role.name}: ${choice.label}`}
                onChange={() =>
                  change(`/api/custom-roles/${role.id}/areas`, { area: choice.area, granted: !allowed },
                    () => allowed
                      ? `${role.name} may no longer change ${choice.label}.`
                      : `${role.name} may now change ${choice.label}.`)} />
              {choice.label}
            </label>
          );
        })}
      </fieldset>
      <fieldset className="flex flex-wrap gap-x-4 gap-y-1 text-sm" aria-label={`Who holds ${role.name}`}>
        {members.map((member) => {
          const held = holds(member.staff_id);
          return (
            <label key={member.staff_id} className="flex items-center gap-1.5">
              <input type="checkbox" checked={held} disabled={!admin}
                aria-label={`${member.name} holds ${role.name}`}
                onChange={() =>
                  change(`/api/custom-roles/${role.id}/staff`, { staff_id: member.staff_id, granted: !held },
                    () => held
                      ? `${member.name} no longer holds ${role.name}.`
                      : `${member.name} now holds ${role.name}.`)} />
              {member.name}
            </label>
          );
        })}
      </fieldset>
    </li>
  );
}
