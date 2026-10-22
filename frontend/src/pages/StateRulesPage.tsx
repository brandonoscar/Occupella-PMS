import { Scale } from "lucide-react";
import { useEffect, useState } from "react";
import type { Me } from "../api";
import { ApiError, api } from "../api";
import { Badge } from "../components/Badge";
import { EmptyState } from "../components/EmptyState";
import { Notice } from "../components/Notice";
import { Card, PageHeader, Shell } from "../components/Shell";
import { Wide } from "../components/Wide";
import { usDate } from "../money";
import type { Rule, Table } from "../stateRules";
import { HOW } from "../stateRules";
import { STAFF_NAV } from "./staffNav";

// Settings → State rules (q142): the numbers some states' statutes set that the PMS warns about
// where staff set the terms (late fees on the property's page, a rent change on the lease's, a
// visit on the work order's), each with its statute and how it was checked. Read only; no
// attorney has reviewed it, and the page says so first.
export function StateRulesPage({ me }: { me: Me }) {
  const [table, setTable] = useState<Table | null>(null);
  const [error, setError] = useState("");
  useEffect(() => {
    api.get<Table>("/api/state-rules")
      .then(setTable)
      .catch((failed) => setError(failed instanceof ApiError ? failed.detail : String(failed)));
  }, []);
  return (
    <Shell me={me} nav={STAFF_NAV}>
      <PageHeader title="State rules" />
      {table === null ? (
        <Notice tone={error ? "error" : "info"}>{error || "Loading"}</Notice>
      ) : (
        <>
          <Card title="What this is">
            <p className="text-base font-medium text-caution" data-testid="not-reviewed">{table.note}</p>
            <p className="mt-2 text-base text-ink-muted">
              Where you set late fees, a rent change or a visit into a home, the PMS checks it
              against the property&apos;s state below (from the property&apos;s address) and warns.
              It never refuses a save. Security deposit rules, rent caps and city rules are not
              checked here. Researched {usDate(table.checked_on)}.
            </p>
          </Card>
          <Card title="By state">
            {table.states.length === 0 ? (
              <EmptyState icon={Scale} title="No state rules yet." />
            ) : (
              <Wide>
                <table className="w-full text-base" aria-label="State rules">
                  <thead>
                    <tr>
                      <th className="text-left">State</th>
                      <th className="text-left">Late fees</th>
                      <th className="text-left">Notice of a rent increase</th>
                      <th className="text-left">Notice before entering</th>
                    </tr>
                  </thead>
                  <tbody>
                    {table.states.map((row) => (
                      <tr key={row.state} aria-label={row.name}>
                        <td className="align-top">{row.name} ({row.state})</td>
                        <RuleCell rule={row.late_fee} />
                        <RuleCell rule={row.rent_increase} />
                        <RuleCell rule={row.entry} />
                      </tr>
                    ))}
                  </tbody>
                </table>
              </Wide>
            )}
          </Card>
        </>
      )}
    </Shell>
  );
}

function RuleCell({ rule }: { rule: Rule | null }) {
  if (rule === null) return <td className="align-top text-sm text-ink-subtle">No number found</td>;
  return (
    <td className="align-top text-sm">
      <span className="first-letter:uppercase">{rule.words}.</span>{" "}
      <a className="text-iris hover:text-iris-hover hover:underline" href={rule.url} target="_blank" rel="noreferrer">
        {rule.cite}
      </a>
      <span className="text-ink-muted">, {HOW[rule.how]}</span>
      {rule.how === "secondary" && <> <Badge tone="caution">Not verified</Badge></>}
    </td>
  );
}
