import { useEffect, useState } from "react";
import { ApiError, api } from "../api";
import { Button } from "../components/Button";
import { Field } from "../components/Field";
import { Notice } from "../components/Notice";
import { Card, Wordmark } from "../components/Shell";
import type { Listing } from "../leasing";
import { money } from "../money";

const MAX_APPLICANTS = 6;

type Person = { name: string; email: string };

// The public application page: /apply#<token>, from a link the property manager posted. No
// login; the answer is a status link the applicant keeps.
export function ApplyPage() {
  const token = window.location.hash.slice(1);
  const [listing, setListing] = useState<Listing | null>(null);
  const [moveIn, setMoveIn] = useState("");
  const [income, setIncome] = useState("");
  const [people, setPeople] = useState<Person[]>([{ name: "", email: "" }]);
  const [statusUrl, setStatusUrl] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    api
      .post<Listing>("/api/apply/view", { token })
      .then(setListing)
      .catch((failed) => setError(failed instanceof ApiError ? failed.detail : String(failed)));
  }, [token]);

  function change(n: number, key: keyof Person, value: string) {
    setPeople(people.map((person, i) => (i === n ? { ...person, [key]: value } : person)));
  }

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError("");
    try {
      const done = await api.post<{ status_url: string }>("/api/apply", {
        token,
        move_in_on: moveIn,
        monthly_income: income,
        applicants: people,
      });
      setStatusUrl(done.status_url);
    } catch (failed) {
      setError(failed instanceof ApiError ? failed.detail : String(failed));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="mx-auto flex max-w-2xl flex-col gap-6 px-6 py-10">
      <Wordmark />
      {listing === null ? (
        <Notice tone={error ? "error" : "info"}>{error || "Loading"}</Notice>
      ) : statusUrl ? (
        <Card title="Application sent">
          <p className="mb-3">
            {listing.pmc} has your application. Keep this link to see where it stands:
          </p>
          <p className="font-mono text-sm break-all" data-testid="status-url">
            {statusUrl}
          </p>
        </Card>
      ) : (
        <Card title={`Apply for ${listing.property}, ${listing.unit}`}>
          <p className="mb-6 text-ink-muted">
            {listing.pmc} offers it at {money(listing.monthly_rent)} a month. The screening
            company may ask each of you for more by email; this form asks only for what is
            below.
          </p>
          <form className="flex flex-col gap-4" onSubmit={submit}>
            <Field label="Move-in day" id="move-in" type="date" required value={moveIn}
              onChange={(e) => setMoveIn(e.target.value)} />
            <Field label="Monthly income of everyone applying, together" id="income"
              inputMode="decimal" required value={income}
              onChange={(e) => setIncome(e.target.value)} />
            {people.map((person, n) => (
              <fieldset key={n} className="flex flex-col gap-3 border-t border-line pt-4">
                <legend className="text-sm text-ink-subtle">
                  {n === 0 ? "You" : `Applicant ${n + 1}`}
                </legend>
                <Field label="Full name" id={`name-${n}`} required value={person.name}
                  onChange={(e) => change(n, "name", e.target.value)} />
                <Field label="Email" id={`email-${n}`} type="email" required value={person.email}
                  onChange={(e) => change(n, "email", e.target.value)} />
              </fieldset>
            ))}
            {people.length < MAX_APPLICANTS && (
              <div>
                <Button type="button" variant="ghost"
                  onClick={() => setPeople([...people, { name: "", email: "" }])}>
                  Add someone else applying
                </Button>
              </div>
            )}
            <Notice tone="error">{error}</Notice>
            <div>
              <Button type="submit" disabled={busy}>
                Send application
              </Button>
            </div>
          </form>
        </Card>
      )}
    </div>
  );
}
