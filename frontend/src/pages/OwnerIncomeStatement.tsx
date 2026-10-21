import { useEffect, useState } from "react";
import { ApiError, api } from "../api";
import { Button, LinkButton } from "../components/Button";
import { Field } from "../components/Field";
import { Notice } from "../components/Notice";
import { Card } from "../components/Shell";
import type { IncomeStatement, Line } from "../incomeStatement";
import { groupIncomeStatement } from "../incomeStatement";
import { money } from "../money";
import type { YearEndData } from "../yearEnd";
import { lastYear } from "../yearEnd";
import { Form } from "../components/Form";

const AMOUNT = "text-right";

function Rows({ lines }: { lines: Line[] }) {
  return (
    <>
      {lines.map((line) => (
        <tr key={line.label}>
          <td className="pl-6 text-left">{line.label}</td>
          <td className={AMOUNT}>{money(line.amount)}</td>
        </tr>
      ))}
    </>
  );
}

function Total({ label, amount }: { label: string; amount: string }) {
  return (
    <tr>
      <td className="text-left font-medium">{label}</td>
      <td className={`${AMOUNT} font-medium`}>{money(amount)}</td>
    </tr>
  );
}

// The signed-in owner's income statement for a year, cash basis: what came in and went out for
// each property and across them, and the Schedule E lines each amount goes on. Their
// accountant files; the PMS files nothing.
export function OwnerIncomeStatement() {
  const [year, setYear] = useState(String(lastYear(new Date())));
  const [shown, setShown] = useState<IncomeStatement | null>(null);
  const [error, setError] = useState("");

  function load(which: string) {
    setError("");
    setShown(null);
    api
      .get<YearEndData>(`/api/owner/income-statement?${new URLSearchParams({ year: which })}`)
      .then((data) => setShown(groupIncomeStatement(data)))
      .catch((failed) => setError(failed instanceof ApiError ? failed.detail : String(failed)));
  }
  useEffect(() => load(year), []); // last year's first; "Show" loads another

  const download = (format: string) =>
    `/api/owner/income-statement/download?${new URLSearchParams({ year, format })}`;

  return (
    <Card title="Income statement">
      <Form
        className="mb-6 flex flex-wrap items-end gap-4"
        onSubmit={(event) => {
          event.preventDefault();
          load(year);
        }}
      >
        <Field label="Year" id="income-statement-year" type="number" min={1900} max={9998} required
          value={year} onChange={(e) => setYear(e.target.value)} />
        <Button type="submit">Show</Button>
        <div className="flex gap-2">
          <LinkButton href={download("pdf")}>PDF</LinkButton>
          <LinkButton href={download("csv")}>CSV</LinkButton>
          <LinkButton href={download("xlsx")}>Excel</LinkButton>
        </div>
      </Form>
      <Notice tone="error">{error}</Notice>
      {shown && (
        <div className="flex flex-col gap-6">
          <p className="text-sm text-ink-subtle">{shown.period}</p>
          {shown.sections.map((section) => (
            <section key={section.name} aria-label={`Income statement, ${section.name}`}
              className="flex flex-col gap-3">
              <table className="w-full text-base">
                <caption className="pb-2 text-left text-md font-medium">{section.name}</caption>
                <tbody>
                  <Rows lines={section.income} />
                  <Total label="Total income" amount={section.totalIncome} />
                  <Rows lines={section.expenses} />
                  <Total label="Total expenses" amount={section.totalExpenses} />
                  <Total label="Net income" amount={section.net} />
                </tbody>
              </table>
              <ul className="flex flex-col gap-1 text-sm text-ink-muted" aria-label={`Schedule E, ${section.name}`}>
                {section.scheduleE.map((line) => (
                  <li key={line.label} className="flex justify-between gap-4">
                    <span>{line.label}</span>
                    <span>{money(line.amount)}</span>
                  </li>
                ))}
              </ul>
            </section>
          ))}
          <p className="text-sm text-ink-subtle">
            Money collected and paid out in the year. Each amount names the Schedule E line it
            goes on, for your accountant. This statement files nothing.
          </p>
        </div>
      )}
    </Card>
  );
}
