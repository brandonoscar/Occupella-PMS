import { useEffect, useState } from "react";
import type { Me } from "../api";
import { ApiError, api } from "../api";
import { Button, LinkButton } from "../components/Button";
import { Field } from "../components/Field";
import { Notice } from "../components/Notice";
import { Select } from "../components/Select";
import { Card, PageHeader, Shell } from "../components/Shell";
import { Wide } from "../components/Wide";
import type { Budget, BudgetLine } from "../budgets";
import { budgetVsActualHref, lineLabel, monthly, spread } from "../budgets";
import { money } from "../money";
import type { OwnerMoney } from "../payables";
import { STAFF_NAV } from "./staffNav";
import { useRecord } from "./useRecord";

// Staff's budgets: what each owner's property is expected to bring in and cost in a year, on
// the income statement's own lines, spread over its months; and the budget against what
// happened, to download. A budget moves no money.
export function BudgetsPage({ me }: { me: Me }) {
  const [owners, setOwners] = useState<OwnerMoney[] | null>(null);
  const [account, setAccount] = useState("");
  const [year, setYear] = useState(String(new Date().getUTCFullYear()));
  const [budget, setBudget] = useState<Budget | null>(null);
  const [error, setError] = useState("");
  const failed = (why: unknown) => setError(why instanceof ApiError ? why.detail : String(why));

  useEffect(() => {
    api
      .get<OwnerMoney[]>("/api/payables/owners")
      .then((found) => {
        setOwners(found);
        setAccount(found[0]?.account ?? "");
      })
      .catch(failed);
  }, []);

  function load() {
    if (!account || !/^\d{4}$/.test(year)) return;
    setError("");
    api
      .get<Budget>(`/api/budgets?${new URLSearchParams({ account, year })}`)
      .then(setBudget)
      .catch((why) => {
        setBudget(null);
        failed(why);
      });
  }
  useEffect(load, [account, year]);

  return (
    <Shell me={me} nav={STAFF_NAV}>
      <PageHeader title="Budgets" description="What each owner's property should bring in and cost, against what happened." />
      <Card title="Budget">
        {owners === null ? (
          <Notice tone={error ? "error" : "info"}>{error || "Loading"}</Notice>
        ) : owners.length === 0 ? (
          <Notice tone="info">No owner has a property account yet. Add one in Setup.</Notice>
        ) : (
          <div className="flex flex-col gap-4">
            <div className="flex flex-wrap items-end gap-3">
              <Select label="Owner and property" id="budget-account" value={account}
                onChange={(e) => setAccount(e.target.value)}>
                {owners.map((o) => (
                  <option key={o.account} value={o.account}>
                    {o.owner}, {o.property}
                  </option>
                ))}
              </Select>
              <Field label="Year" id="budget-year" type="number" min={1900} max={9998} value={year}
                onChange={(e) => setYear(e.target.value)} />
              {budget && (
                <LinkButton href={budgetVsActualHref(budget.owner_id, budget.year)}>
                  {`${budget.owner}'s budget against actual, ${budget.year} (Excel)`}
                </LinkButton>
              )}
            </div>
            <Notice tone="error">{error}</Notice>
            {budget && <BudgetTable budget={budget} refresh={load} />}
          </div>
        )}
      </Card>
    </Shell>
  );
}

function BudgetTable({ budget, refresh }: { budget: Budget; refresh: () => void }) {
  const { said, error, record } = useRecord(refresh);
  return (
    <>
      <Wide>
        <table className="w-full text-left text-sm">
          <thead className="text-ink-muted">
            <tr>
              <th className="py-2 font-medium">Line</th>
              <th className="py-2 font-medium">A year</th>
              <th className="py-2 font-medium">By month</th>
              <th className="py-2" />
            </tr>
          </thead>
          <tbody>
            {budget.lines.map((line) => (
              <BudgetRow key={`${line.line} ${line.expense_account_id ?? ""}`} budget={budget} line={line}
                save={(months) =>
                  record<{ changed: number }>(
                    "/api/budgets",
                    {
                      account: budget.account,
                      year: budget.year,
                      line: line.line,
                      expense_account_id: line.expense_account_id,
                      months,
                    },
                    (answer) => `${lineLabel(line)}: ${answer.changed} months changed in ${budget.year}.`,
                  )} />
            ))}
          </tbody>
        </table>
      </Wide>
      <Notice tone="info">{said}</Notice>
      <Notice tone="error">{error}</Notice>
    </>
  );
}

function BudgetRow({ budget, line, save }: {
  budget: Budget;
  line: BudgetLine;
  save: (months: string[]) => void;
}) {
  const [year, setYear] = useState(line.year);
  const [wrong, setWrong] = useState("");
  // The year the server holds, whenever it changes (another account or year, or a save).
  useEffect(() => setYear(line.year), [line.year, budget.account, budget.year]);
  const label = lineLabel(line);
  return (
    <tr className="border-t border-line">
      <td className="py-2">{label}</td>
      <td className="py-2">
        <input
          aria-label={`${label}, a year`}
          inputMode="decimal"
          className="h-8 w-32 rounded-sm border border-line-strong bg-sunken px-2 text-sm"
          value={year}
          onChange={(e) => setYear(e.target.value)}
        />
        {wrong && <span className="ml-2 text-sm text-danger">{wrong}</span>}
      </td>
      <td className="py-2 text-ink-muted">
        {line.months.every((m) => m === "0.00") ? "Not budgeted" : monthly(line) === "varies by month"
          ? "Varies by month"
          : `${money(line.months[0] ?? "0")} a month`}
      </td>
      <td className="py-2 text-right">
        <Button size="sm" variant="secondary"
          onClick={() => {
            try {
              setWrong("");
              save(spread(year));
            } catch {
              setWrong("Enter an amount, like 1200.00");
            }
          }}>
          Save {label.toLowerCase()}
        </Button>
      </td>
    </tr>
  );
}
