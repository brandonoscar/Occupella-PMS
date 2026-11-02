import { Calculator } from "lucide-react";
import { useEffect, useState } from "react";
import type { Me } from "../api";
import { ApiError, api } from "../api";
import { Button, LinkButton } from "../components/Button";
import { EmptyState } from "../components/EmptyState";
import { Field } from "../components/Field";
import { Form } from "../components/Form";
import { Notice } from "../components/Notice";
import { Said } from "../components/Said";
import { Select } from "../components/Select";
import { PageHeader, Shell } from "../components/Shell";
import { Tabs } from "../components/Tabs";
import { Wide } from "../components/Wide";
import type { Budget } from "../budgets";
import { MONTHS, budgetVsActualHref, isAmount, lineLabel, spread, total } from "../budgets";
import { latest } from "../latest";
import { money } from "../money";
import type { OwnerMoney } from "../payables";
import { fromCents, toCents } from "../reconcile";
import type { Preview } from "../reports";
import { STAFF_NAV } from "./staffNav";
import { useRecord } from "./useRecord";

type View = "budget" | "actual";
const CELL = "h-8 w-24 rounded-sm border border-line-strong bg-sunken px-2 text-right text-sm";

// Budgets (ADR 0006, step 12): a property's year in one grid, its income and expense lines by
// month with each line's total and the net income, saved at once; or the same lines against what
// happened (the budget vs actual report, narrowed to the property). A property with more than one
// owner budgets each owner's share apart. A budget moves no money.
export function BudgetsPage({ me }: { me: Me }) {
  const [owners, setOwners] = useState<OwnerMoney[] | null>(null);
  const [property, setProperty] = useState("");
  const [account, setAccount] = useState("");
  const [year, setYear] = useState(String(new Date().getUTCFullYear()));
  const [view, setView] = useState<View>("budget");
  const [budget, setBudget] = useState<Budget | null>(null);
  const [loads, setLoads] = useState(0);
  const [runs, setRuns] = useState(0);
  const [error, setError] = useState("");
  const { said, error: refused, record } = useRecord(() => setRuns((n) => n + 1));
  const failed = (why: unknown) => setError(why instanceof ApiError ? why.detail : String(why));

  useEffect(() => {
    api.get<OwnerMoney[]>("/api/payables/owners").then(setOwners).catch(failed);
  }, []);
  const properties = [...new Map((owners ?? []).map((o) => [o.property_id, o.property])).entries()]
    .sort(([, a], [, b]) => (a < b ? -1 : a > b ? 1 : 0));
  const chosenProperty = properties.some(([id]) => id === property) ? property : (properties[0]?.[0] ?? "");
  const shares = (owners ?? []).filter((o) => o.property_id === chosenProperty);
  const share = shares.find((o) => o.account === account) ?? shares[0];

  useEffect(() => {
    const run = latest();
    if (!share || !/^\d{4}$/.test(year)) return;
    setError("");
    api
      .get<Budget>(`/api/budgets?${new URLSearchParams({ account: share.account, year })}`)
      .then(run.keep((found) => {
        setBudget(found);
        setLoads((n) => n + 1);
      }))
      .catch(run.keep((why) => {
        setBudget(null);
        failed(why);
      }));
    return run.stop;
  }, [share?.account, year, runs]);

  return (
    <Shell me={me} nav={STAFF_NAV}>
      <PageHeader title="Budgets" actions={budget && (
        <LinkButton href={budgetVsActualHref(budget.owner_id, budget.year)}>Export</LinkButton>
      )} />
      {owners === null ? (
        <Notice tone={error ? "error" : "info"}>{error || "Loading"}</Notice>
      ) : properties.length === 0 ? (
        <EmptyState icon={Calculator} title="No property has an owner yet" />
      ) : (
        <div className="flex flex-col gap-4">
          <div className="flex flex-wrap items-end gap-3">
            <Select label="Property" id="budget-property" value={chosenProperty}
              onChange={(e) => { setProperty(e.target.value); setAccount(""); }}>
              {properties.map(([id, name]) => <option key={id} value={id}>{name}</option>)}
            </Select>
            {shares.length > 1 && (
              <Select label="Owner" id="budget-owner" value={share?.account ?? ""} onChange={(e) => setAccount(e.target.value)}>
                {shares.map((o) => <option key={o.account} value={o.account}>{o.owner}</option>)}
              </Select>
            )}
            <Field label="Year" id="budget-year" type="number" min={1900} max={9998} value={year}
              onChange={(e) => setYear(e.target.value)} />
          </div>
          <Tabs label="View" value={view} onChange={setView}
            tabs={[{ value: "budget", label: "Budget" }, { value: "actual", label: "Budget vs actual" }]} />
          <Said>{said}</Said>
          <Notice tone="error">{error}</Notice>
          {budget && share && (view === "budget" ? (
            <BudgetGrid key={loads} budget={budget} refused={refused}
              save={(body) => record<{ changed: number }>("/api/budgets", body,
                (answer) => `Saved ${budget.property}'s ${budget.year} budget: ${answer.changed} months changed.`)} />
          ) : (
            <VersusActual budget={budget} propertyId={share.property_id} />
          ))}
        </div>
      )}
    </Shell>
  );
}

type Save = (body: unknown) => Promise<boolean>;

// Every line by month, as typed; a line's total spreads over its months when typed in.
function BudgetGrid({ budget, refused, save }: { budget: Budget; refused: string; save: Save }) {
  const [months, setMonths] = useState(() => budget.lines.map((line) => [...line.months]));
  const [totals, setTotals] = useState<Record<number, string>>({});
  const [wrong, setWrong] = useState("");
  const labels = budget.lines.map(lineLabel);
  const income = budget.lines.map((line) => line.label.startsWith("income:"));
  const set = (row: number, next: string[]) => setMonths((all) => all.map((m, i) => (i === row ? next : m)));
  const net = (column: (row: string[]) => string) =>
    fromCents(months.reduce((sum, row, i) => sum + (income[i] ? 1 : -1) * toCents(column(row)), 0));
  const changed = months.some((row, i) => row.some((m, j) => m !== budget.lines[i]!.months[j]));
  const spreadTotal = (row: number) => {
    const typed = totals[row];
    setTotals(({ [row]: _, ...rest }) => rest);
    if (typed === undefined || !isAmount(typed) || typed === total(months[row]!)) return;
    set(row, spread(typed.replace(/,/g, "")));
  };

  return (
    <Form className="flex flex-col gap-3" onSubmit={(event) => {
      event.preventDefault();
      setWrong("");
      for (const [i, row] of months.entries()) {
        const j = row.findIndex((m) => !isAmount(m));
        if (j >= 0) return setWrong(`${labels[i]}, ${MONTHS[j]}: enter an amount, like 1200.00.`);
      }
      return save({
        account: budget.account,
        year: budget.year,
        lines: budget.lines.map((line, i) => ({
          line: line.line,
          expense_account_id: line.expense_account_id,
          months: months[i]!.map((m) => m.replace(/,/g, "").trim()),
        })),
      });
    }}>
      <Wide>
        <table className="w-full text-sm" aria-label="Budget">
          <thead>
            <tr>
              <th className="text-left">Line</th>
              {MONTHS.map((m) => <th key={m} className="text-right"><abbr title={m} className="no-underline">{m.slice(0, 3)}</abbr></th>)}
              <th className="text-right">Total</th>
            </tr>
          </thead>
          <tbody>
            {budget.lines.map((line, i) => (
              <tr key={`${line.line} ${line.expense_account_id ?? ""}`} aria-label={labels[i]}>
                <th scope="row" className="whitespace-nowrap text-left font-normal">{labels[i]}</th>
                {months[i]!.map((m, j) => (
                  <td key={j}>
                    <input aria-label={`${labels[i]}, ${MONTHS[j]}`} inputMode="decimal" className={CELL}
                      aria-invalid={!isAmount(m) || undefined} value={m}
                      onChange={(e) => set(i, months[i]!.map((old, k) => (k === j ? e.target.value : old)))} />
                  </td>
                ))}
                <td>
                  <input aria-label={`${labels[i]}, total`} inputMode="decimal" className={`${CELL} font-medium`}
                    value={totals[i] ?? total(months[i]!)}
                    onChange={(e) => setTotals((all) => ({ ...all, [i]: e.target.value }))}
                    onBlur={() => spreadTotal(i)}
                    onKeyDown={(e) => {
                      if (e.key !== "Enter") return;
                      e.preventDefault();
                      spreadTotal(i);
                    }} />
                </td>
              </tr>
            ))}
          </tbody>
          <tfoot>
            <tr aria-label="Net income" className="border-t border-line font-medium">
              <th scope="row" className="text-left">Net income</th>
              {MONTHS.map((m, j) => <td key={m} className="text-right">{money(net((row) => (isAmount(row[j]!) ? row[j]!.replace(/,/g, "") : "0")))}</td>)}
              <td className="text-right">{money(net(total))}</td>
            </tr>
          </tfoot>
        </table>
      </Wide>
      <div className="flex items-center gap-3">
        <Button type="submit" says="Saves the whole year, all or none; earlier figures stay in the history. A budget moves no money.">
          Save budget
        </Button>
        {changed && <span className="text-sm text-ink-muted">Unsaved changes</span>}
      </div>
      <Notice tone="error">{wrong || refused}</Notice>
    </Form>
  );
}

// The year's lines against what happened, as the budget vs actual report writes them for the
// property (cash basis).
function VersusActual({ budget, propertyId }: { budget: Budget; propertyId: string }) {
  const [preview, setPreview] = useState<Preview | null>(null);
  const [error, setError] = useState("");
  useEffect(() => {
    const run = latest();
    setPreview(null);
    setError("");
    api
      .get<Preview>(budgetVsActualHref(budget.owner_id, budget.year, "json", propertyId))
      .then(run.keep(setPreview))
      .catch(run.keep((why) => setError(why instanceof ApiError ? why.detail : String(why))));
    return run.stop;
  }, [budget.owner_id, budget.year, propertyId]);
  if (preview === null) return <Notice tone={error ? "error" : "info"}>{error || "Loading"}</Notice>;
  const at = (name: string) => preview.columns.indexOf(name);
  const rows = preview.rows.filter((row) => row[at("property")] === budget.property);
  const label = (item: string) => item.charAt(0).toUpperCase() + item.slice(1);
  return (
    <Wide>
      <table className="w-full text-base" aria-label="Budget vs actual">
        <thead>
          <tr>
            <th className="text-left">Line</th>
            <th className="text-right">Budget</th>
            <th className="text-right">Actual</th>
            <th className="text-right">Actual less budget</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => {
            const item = row[at("item")] ?? "";
            const sum = !item.includes(":");
            return (
              <tr key={item} aria-label={label(item)} className={sum ? "font-medium" : undefined}>
                <td>{label(item)}</td>
                <td className="text-right">{money(row[at("budget")] ?? "")}</td>
                <td className="text-right">{money(row[at("actual")] ?? "")}</td>
                <td className="text-right">{money(row[at("actual_less_budget")] ?? "")}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </Wide>
  );
}
