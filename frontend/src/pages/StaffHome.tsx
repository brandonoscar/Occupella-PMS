import { useEffect, useState } from "react";
import type { Me } from "../api";
import { ApiError, api } from "../api";
import { Button, LinkButton } from "../components/Button";
import { Notice } from "../components/Notice";
import { Card, Shell } from "../components/Shell";
import { day } from "../money";
import { LeasingCard } from "./LeasingCard";
import { MaintenanceCard } from "./MaintenanceCard";
import { OffersCard } from "./OffersCard";

type Person = {
  kind: "staff" | "owner" | "tenant";
  person_id: string;
  name: string;
  email: string | null;
  status: "no login" | "invited" | "active" | "disabled";
};

type Link = { login_id: string; url: string; expires_at: string };

// Staff invite the PMC's owners and tenants (and each other) and download reports. Each invite
// link is shown once, to copy and send yourself: the PMS sends no email yet.
export function StaffHome({ me }: { me: Me }) {
  const [people, setPeople] = useState<Person[] | null>(null);
  const [emails, setEmails] = useState<Record<string, string>>({});
  const [links, setLinks] = useState<Record<string, string>>({});
  const [error, setError] = useState("");
  // Counts the leasing card's changes, so the offers card sees a new approval.
  const [decided, setDecided] = useState(0);
  const today = day(new Date());

  function refresh() {
    api
      .get<Person[]>("/api/people")
      .then(setPeople)
      .catch((failed) => setError(failed instanceof ApiError ? failed.detail : String(failed)));
  }
  useEffect(refresh, []);

  async function invite(person: Person) {
    setError("");
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
    <Shell me={me}>
      <h1 className="font-display text-xl font-medium">{me.pmc_name}</h1>
      <Card title="Downloads">
        <div className="flex flex-wrap gap-2">
          <LinkButton href={`/api/reports/trust_report_rent_roll?arg=${today}&format=pdf`}>
            Rent roll (PDF)
          </LinkButton>
          <LinkButton href={`/api/reports/trust_report_trial_balance?arg=${today}&format=xlsx`}>
            Trial balance (Excel)
          </LinkButton>
          <LinkButton href="/api/ledger.zip">Whole ledger (CSV, zip)</LinkButton>
        </div>
      </Card>
      <Card title="People who can sign in">
        <Notice tone="error">{error}</Notice>
        {people === null ? (
          <Notice tone="info">Loading</Notice>
        ) : (
          <table className="w-full text-base">
            <thead className="text-sm text-ink-subtle">
              <tr>
                <th className="px-3 py-2 text-left">Name</th>
                <th className="px-3 py-2 text-left">Role</th>
                <th className="px-3 py-2 text-left">Email</th>
                <th className="px-3 py-2 text-left">Login</th>
                <th className="px-3 py-2" />
              </tr>
            </thead>
            <tbody>
              {people.map((person) => (
                <tr key={person.person_id} className="border-t border-line align-top">
                  <td className="px-3 py-2">{person.name}</td>
                  <td className="px-3 py-2 text-ink-muted">{person.kind}</td>
                  <td className="px-3 py-2">
                    {person.email ?? (
                      <input
                        aria-label={`Email for ${person.name}`}
                        type="email"
                        className="h-8 w-56 rounded-sm border border-line-strong bg-sunken px-2 text-sm"
                        value={emails[person.person_id] ?? ""}
                        onChange={(e) => setEmails({ ...emails, [person.person_id]: e.target.value })}
                      />
                    )}
                  </td>
                  <td className="px-3 py-2 text-ink-muted">{person.status}</td>
                  <td className="px-3 py-2 text-right">
                    {person.person_id !== me.person_id && person.status !== "disabled" && (
                      <Button variant="secondary" onClick={() => invite(person)}>
                        {person.status === "no login" ? "Invite" : "New invite"}
                      </Button>
                    )}
                    {links[person.person_id] && (
                      <p className="mt-2 max-w-xs font-mono text-xs break-all text-ink-muted">
                        {links[person.person_id]}
                      </p>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Card>
      <MaintenanceCard />
      <LeasingCard onChange={() => setDecided((n) => n + 1)} />
      <OffersCard changed={decided} />
    </Shell>
  );
}
