import { useEffect, useState } from "react";
import type { Me } from "../api";
import { ApiError, api } from "../api";
import { Button } from "../components/Button";
import { Field } from "../components/Field";
import { Notice } from "../components/Notice";
import { Card, PageHeader, Shell } from "../components/Shell";
import type { Member } from "../staff";
import { ROLES, changeLabel, holds } from "../staff";
import { CustomRolesCard } from "./CustomRolesCard";
import { SignersCard } from "./SignersCard";
import { STAFF_NAV } from "./staffNav";
import { useRecord } from "./useRecord";
import { Said } from "../components/Said";
import { Form } from "../components/Form";
import { Badge } from "../components/Badge";
import { SidePanel } from "../components/SidePanel";
import type { Person } from "../logins";
import { LOGIN } from "../logins";
import type { Portal } from "../rentals";
import { InviteForm } from "./PortalCard";

// The PMC's staff and their roles. Every staff member sees who may do what and who can sign in;
// an admin adds staff members, invites them to sign in and changes roles (the database refuses
// anyone else, and keeps one admin).
export function StaffPage({ me }: { me: Me }) {
  const [members, setMembers] = useState<Member[] | null>(null);
  const [error, setError] = useState("");
  const [name, setName] = useState("");
  const [logins, setLogins] = useState<Record<string, { status: Portal; email: string | null }>>({});
  const [inviting, setInviting] = useState<Member | null>(null);
  const { said, error: refused, record } = useRecord(refresh);
  const admin = holds(me, "admin");

  function refresh() {
    api
      .get<Member[]>("/api/staff")
      .then(setMembers)
      .catch((failed) => setError(failed instanceof ApiError ? failed.detail : String(failed)));
    api
      .get<Person[]>("/api/people")
      .then((people) => setLogins(Object.fromEntries(people.filter((p) => p.kind === "staff")
        .map((p) => [p.person_id, { status: p.status, email: p.email }]))))
      .catch(() => setLogins({}));
  }
  useEffect(refresh, []);

  return (
    <Shell me={me} nav={STAFF_NAV}>
      <PageHeader title="Staff and roles" />
      <Card title="Who may do what">
        <ul className="mb-4 flex flex-col gap-1 text-sm text-ink-muted">
          {ROLES.map((r) => (
            <li key={r.role}>
              <span className="font-medium text-ink">{r.label}</span>: {r.does}.
            </li>
          ))}
        </ul>
        {members === null ? (
          <Notice tone={error ? "error" : "info"}>{error || "Loading"}</Notice>
        ) : (
          <table className="w-full text-left text-sm">
            <thead className="text-ink-muted">
              <tr>
                <th className="py-2 font-medium">Staff member</th>
                {ROLES.map((r) => (
                  <th key={r.role} className="py-2 font-medium">
                    {r.label}
                  </th>
                ))}
                <th className="py-2 font-medium">Login</th>
                <th><span className="sr-only">Actions</span></th>
              </tr>
            </thead>
            <tbody>
              {members.map((member) => (
                <tr key={member.staff_id} className="border-t border-line">
                  <td className="py-2">{member.name}</td>
                  {ROLES.map(({ role, label }) => {
                    const held = member.roles.includes(role);
                    return (
                      <td key={role} className="py-2">
                        <input
                          type="checkbox"
                          aria-label={`${member.name}: ${label}`}
                          checked={held}
                          disabled={!admin}
                          onChange={() =>
                            record(`/api/staff/${member.staff_id}/roles`, { role, granted: !held },
                              () => changeLabel(member, role, !held))
                          }
                        />
                      </td>
                    );
                  })}
                  <td className="py-2">
                    {logins[member.staff_id] && (
                      <Badge tone={LOGIN[logins[member.staff_id]!.status].tone}>{LOGIN[logins[member.staff_id]!.status].label}</Badge>
                    )}
                  </td>
                  <td className="py-2 text-right">
                    {admin && member.staff_id !== me.person_id && logins[member.staff_id]?.status !== "disabled" && (
                      <Button size="sm" variant="secondary" onClick={() => setInviting(member)}>Invite</Button>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
        {!admin && <p className="mt-3 text-sm text-ink-subtle">Only an admin changes roles.</p>}
        <Said>{said}</Said>
        <Notice tone="error">{refused}</Notice>
      </Card>
      {members && <CustomRolesCard members={members} admin={admin} />}
      {members && <SignersCard me={me} members={members} />}
      {admin && (
        <Card title="Add a staff member">
          <Form
            className="flex flex-wrap items-end gap-3"
            onSubmit={async (event) => {
              event.preventDefault();
              if (await record<{ id: string }>("/api/staff", { name }, () => `Added ${name}; invite them from their row.`)) {
                setName("");
              }
            }}
          >
            <Field label="Name" id="staff-name" required value={name} onChange={(e) => setName(e.target.value)} />
            <Button type="submit" says="Adds them with no roles and no login. Nothing is sent; invite them from their row.">Add</Button>
          </Form>
        </Card>
      )}
      {inviting && (
        <SidePanel title={`Invite ${inviting.name}`} onClose={() => setInviting(null)}>
          <InviteForm kind="staff" personId={inviting.staff_id} name={inviting.name}
            portal={logins[inviting.staff_id]?.status ?? "no login"} email={logins[inviting.staff_id]?.email ?? null}
            emailOn={me.email_on ?? false} refresh={refresh} />
        </SidePanel>
      )}
    </Shell>
  );
}
