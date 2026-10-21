import { KeyRound } from "lucide-react";
import { useEffect, useState } from "react";
import type { Me } from "../api";
import { ApiError, api } from "../api";
import { Badge } from "../components/Badge";
import { EmptyState } from "../components/EmptyState";
import { Notice } from "../components/Notice";
import { RowMenu } from "../components/RowMenu";
import { Said } from "../components/Said";
import { PageHeader, Shell } from "../components/Shell";
import { Wide } from "../components/Wide";
import type { Person } from "../logins";
import { LOGIN, ROLE, personHref } from "../logins";
import type { Portal } from "../rentals";
import { GoLink } from "./NeedsAttention";
import { STAFF_NAV } from "./staffNav";
import { useRecord } from "./useRecord";

const LINK = "font-medium text-iris hover:text-iris-hover hover:underline";
const FILTER = "h-9 rounded-sm border border-line-strong bg-canvas px-2 text-sm text-ink";
const STATUSES: Portal[] = ["active", "invited", "no login", "disabled"];

// Settings → Logins (ADR 0006, step 12): everyone who may sign in and their login's state, by
// status. Invites are on each person's own page (an owner's, a tenant's, the staff page); here a
// login is only disabled, for good (no new invite brings it back), from its row's menu.
export function LoginsPage({ me }: { me: Me }) {
  const [people, setPeople] = useState<Person[] | null>(null);
  const [status, setStatus] = useState<Portal | "">("");
  const [error, setError] = useState("");
  const { said, error: refused, record } = useRecord(refresh);

  function refresh() {
    api
      .get<Person[]>("/api/people")
      .then(setPeople)
      .catch((failed) => setError(failed instanceof ApiError ? failed.detail : String(failed)));
  }
  useEffect(refresh, []);
  const shown = (people ?? []).filter((p) => !status || p.status === status);

  return (
    <Shell me={me} nav={STAFF_NAV}>
      <PageHeader title="Logins" />
      <div className="flex flex-wrap items-center gap-3">
        <select aria-label="Status" value={status} onChange={(e) => setStatus(e.target.value as Portal | "")} className={FILTER}>
          <option value="">Every status</option>
          {STATUSES.map((s) => <option key={s} value={s}>{LOGIN[s].label}</option>)}
        </select>
      </div>
      <Said>{said}</Said>
      <Notice tone="error">{refused || error}</Notice>
      {people === null ? (
        !error && <Notice tone="info">Loading</Notice>
      ) : shown.length === 0 ? (
        <EmptyState icon={KeyRound} title="No logins here" />
      ) : (
        <Wide>
          <table className="w-full text-base" aria-label="Logins">
            <thead>
              <tr>
                <th className="text-left">Name</th>
                <th className="text-left">Role</th>
                <th className="text-left">Email</th>
                <th className="text-left">Login</th>
                <th><span className="sr-only">Actions</span></th>
              </tr>
            </thead>
            <tbody>
              {shown.map((person) => (
                <tr key={`${person.kind} ${person.person_id}`} aria-label={person.name}>
                  <td><GoLink href={personHref(person)} className={LINK}>{person.name}</GoLink></td>
                  <td className="text-ink-muted">{ROLE[person.kind]}</td>
                  <td className="break-all text-ink-muted">{person.email ?? ""}</td>
                  <td><Badge tone={LOGIN[person.status].tone}>{LOGIN[person.status].label}</Badge></td>
                  <td className="text-right">
                    {person.login_id && person.status !== "disabled" && person.person_id !== me.person_id && (
                      <RowMenu label={person.name} actions={[{
                        label: "Disable", danger: true,
                        confirm: `Disable ${person.name}'s login? They are signed out now and can't sign in again.`,
                        onSelect: () => record(`/api/logins/${person.login_id}/disable`, {},
                          () => `${person.name}'s login is disabled; they are signed out.`),
                      }]} />
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </Wide>
      )}
    </Shell>
  );
}
