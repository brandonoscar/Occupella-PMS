import { useEffect, useState } from "react";
import type { Me } from "../api";
import { ApiError, api } from "../api";
import type { BadgeTone } from "../components/Badge";
import { Badge } from "../components/Badge";
import { Button, LinkButton } from "../components/Button";
import { Notice } from "../components/Notice";
import { Card, PageHeader, Shell } from "../components/Shell";
import { Wide } from "../components/Wide";
import { staffDownloads } from "../reports";
import { LeasingCard } from "./LeasingCard";
import { MaintenanceCard } from "./MaintenanceCard";
import { OffersCard } from "./OffersCard";
import { STAFF_NAV } from "./staffNav";

type Person = {
  kind: "staff" | "owner" | "tenant";
  person_id: string;
  name: string;
  email: string | null;
  login_id: string | null;
  status: "no login" | "invited" | "active" | "disabled";
};

type Link = { login_id: string; url: string; expires_at: string };

// A login's state as a badge: signed in is fine, an invite out is news, none yet is neutral.
export const LOGIN: Record<Person["status"], { label: string; tone: BadgeTone }> = {
  "no login": { label: "No login", tone: "neutral" },
  invited: { label: "Invited", tone: "info" },
  active: { label: "Active", tone: "positive" },
  disabled: { label: "Disabled", tone: "caution" },
};
const ROLE: Record<Person["kind"], string> = { staff: "Staff", owner: "Owner", tenant: "Tenant" };

// Staff invite the PMC's owners and tenants (and each other) and download reports. Each invite
// link is shown once, to copy and send yourself: the PMS sends no email yet. A login is
// disabled for good (no new invite brings it back), so Disable asks before it does anything.
export function StaffHome({ me }: { me: Me }) {
  const [people, setPeople] = useState<Person[] | null>(null);
  const [emails, setEmails] = useState<Record<string, string>>({});
  const [links, setLinks] = useState<Record<string, string>>({});
  const [error, setError] = useState("");
  const [done, setDone] = useState("");
  // The person whose login staff asked to disable, waiting for them to say yes.
  const [disabling, setDisabling] = useState<string | null>(null);
  // Counts the leasing card's changes, so the offers card sees a new approval.
  const [decided, setDecided] = useState(0);

  function refresh() {
    api
      .get<Person[]>("/api/people")
      .then(setPeople)
      .catch((failed) => setError(failed instanceof ApiError ? failed.detail : String(failed)));
  }
  useEffect(refresh, []);

  async function disable(person: Person) {
    setError("");
    setDone("");
    try {
      await api.post(`/api/logins/${person.login_id}/disable`);
      setDisabling(null);
      setDone(`${person.name}'s login is disabled; they are signed out.`);
      refresh();
    } catch (failed) {
      setError(failed instanceof ApiError ? failed.detail : String(failed));
    }
  }

  async function invite(person: Person) {
    setError("");
    setDone("");
    try {
      const link = await api.post<Link>("/api/invites", {
        kind: person.kind,
        person_id: person.person_id,
        email: person.email ?? emails[person.person_id] ?? "",
      });
      setLinks({ ...links, [person.person_id]: link.url });
      refresh();
    } catch (failed) {
      setError(failed instanceof ApiError ? failed.detail : String(failed));
    }
  }

  return (
    <Shell me={me} nav={STAFF_NAV}>
      <PageHeader title={me.pmc_name} description="Reports to download, who can sign in, maintenance requests, leasing and lease offers." />
      <Card title="Downloads">
        <div className="flex flex-wrap gap-2">
          {staffDownloads(new Date()).map((download) => (
            <LinkButton key={download.label} href={download.href}>
              {download.label}
            </LinkButton>
          ))}
        </div>
      </Card>
      <Card title="People who can sign in">
        <Notice tone="error">{error}</Notice>
        <Notice tone="info">{done}</Notice>
        {people === null ? (
          <Notice tone="info">Loading</Notice>
        ) : (
          <Wide>
            <table className="w-full text-base">
              <thead>
                <tr>
                  <th className="text-left">Name</th>
                  <th className="text-left">Role</th>
                  <th className="text-left">Email</th>
                  <th className="text-left">Login</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {people.map((person) => (
                  <tr key={person.person_id}>
                    <td>{person.name}</td>
                    <td className="text-ink-muted">{ROLE[person.kind]}</td>
                    <td>
                      {person.email ?? (
                        <input
                          aria-label={`Email for ${person.name}`}
                          type="email"
                          placeholder="Email to invite"
                          className="h-8 w-56 rounded-sm border border-line-strong bg-sunken px-2 text-sm placeholder:text-ink-faint"
                          value={emails[person.person_id] ?? ""}
                          onChange={(e) => setEmails({ ...emails, [person.person_id]: e.target.value })}
                        />
                      )}
                    </td>
                    <td>
                      <Badge tone={LOGIN[person.status].tone}>{LOGIN[person.status].label}</Badge>
                    </td>
                    <td className="text-right">
                      {person.person_id !== me.person_id && person.status !== "disabled" && (
                        <span className="inline-flex gap-2">
                          <Button variant="secondary" size="sm" onClick={() => invite(person)}>
                            {person.status === "no login" ? "Invite" : "New invite"}
                          </Button>
                          {person.login_id && disabling !== person.person_id && (
                            <Button variant="ghost" size="sm" onClick={() => setDisabling(person.person_id)}>
                              Disable
                            </Button>
                          )}
                        </span>
                      )}
                      {disabling === person.person_id && (
                        <div className="mt-2 flex flex-wrap items-center justify-end gap-2 text-left">
                          <span className="text-sm text-ink-muted">
                            {person.name} can't sign in again, and is signed out now.
                          </span>
                          <Button variant="secondary" size="sm" onClick={() => disable(person)}>
                            Disable login
                          </Button>
                          <Button variant="ghost" size="sm" onClick={() => setDisabling(null)}>
                            Cancel
                          </Button>
                        </div>
                      )}
                      {links[person.person_id] && (
                        <p className="mt-2 ml-auto max-w-xs text-left font-mono text-xs break-all text-ink-muted">
                          {links[person.person_id]}
                        </p>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </Wide>
        )}
      </Card>
      <MaintenanceCard />
      <LeasingCard pmcId={me.pmc_id} onChange={() => setDecided((n) => n + 1)} />
      <OffersCard changed={decided} />
    </Shell>
  );
}
