import { useEffect, useState } from "react";
import type { Me } from "../api";
import { ApiError, api } from "../api";
import { Button } from "../components/Button";
import { Field } from "../components/Field";
import { Notice } from "../components/Notice";
import { Card, Shell } from "../components/Shell";
import type { Member } from "../staff";
import { ROLES, changeLabel, holds } from "../staff";
import { STAFF_NAV } from "./staffNav";
import { useRecord } from "./useRecord";

// The PMC's staff and their roles. Every staff member sees who may do what; an admin adds staff
// members and changes roles (the database refuses anyone else, and keeps one admin). Invites go
// out from the Overview's list of people who can sign in.
export function StaffPage({ me }: { me: Me }) {
  const [members, setMembers] = useState<Member[] | null>(null);
  const [error, setError] = useState("");
  const [name, setName] = useState("");
  const { said, error: refused, record } = useRecord(refresh);
  const admin = holds(me, "admin");

  function refresh() {
    api
      .get<Member[]>("/api/staff")
      .then(setMembers)
      .catch((failed) => setError(failed instanceof ApiError ? failed.detail : String(failed)));
  }
  useEffect(refresh, []);

  return (
    <Shell me={me} nav={STAFF_NAV}>
      <h1 className="font-display text-xl font-medium">Staff</h1>
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
                </tr>
              ))}
            </tbody>
          </table>
        )}
        {!admin && <p className="mt-3 text-sm text-ink-subtle">Only an admin changes roles.</p>}
        <Notice tone="info">{said}</Notice>
        <Notice tone="error">{refused}</Notice>
      </Card>
      {admin && (
        <Card title="Add a staff member">
          <form
            className="flex flex-wrap items-end gap-3"
            onSubmit={async (event) => {
              event.preventDefault();
              if (await record<{ id: string }>("/api/staff", { name }, () => `Added ${name}; invite them from the Overview.`)) {
                setName("");
              }
            }}
          >
            <Field label="Name" id="staff-name" required value={name} onChange={(e) => setName(e.target.value)} />
            <Button type="submit">Add</Button>
          </form>
        </Card>
      )}
    </Shell>
  );
}
