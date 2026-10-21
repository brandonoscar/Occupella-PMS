import { BookOpen } from "lucide-react";
import { useState } from "react";
import type { Me } from "../api";
import { Button } from "../components/Button";
import { EmptyState } from "../components/EmptyState";
import { Field } from "../components/Field";
import { Form } from "../components/Form";
import { Notice } from "../components/Notice";
import { Said } from "../components/Said";
import { Select } from "../components/Select";
import { PageHeader, Shell } from "../components/Shell";
import { SidePanel } from "../components/SidePanel";
import { Wide } from "../components/Wide";
import type { Portfolio } from "../setup";
import { ChargeTypesCard } from "./ChargeTypes";
import { usePortfolio } from "./SetupPanels";
import { STAFF_NAV } from "./staffNav";
import { useRecord } from "./useRecord";

// Settings → Chart of accounts (ADR 0006, step 12): the accounts bills go in, each on its line of
// Schedule E, so an owner's income statement breaks expenses out the way their accountant
// reports them. Add account opens a side panel. An account moves no money. Below, the company's
// charge types, which do the same for the fees leases are charged.
export function ChartOfAccountsPage({ me }: { me: Me }) {
  const [books, error, reload] = usePortfolio();
  const [adding, setAdding] = useState(false);
  const [said, setSaid] = useState("");
  const lineLabel = (books: Portfolio, line: number) =>
    `${line}: ${books.schedule_e_lines.find((l) => l.line === line)?.label ?? ""}`;
  return (
    <Shell me={me} nav={STAFF_NAV}>
      <PageHeader title="Chart of accounts" actions={<Button onClick={() => setAdding(true)}>Add account</Button>} />
      <Said>{said}</Said>
      {books === null ? (
        <Notice tone={error ? "error" : "info"}>{error || "Loading"}</Notice>
      ) : books.expense_accounts.length === 0 ? (
        <EmptyState icon={BookOpen} title="No expense accounts yet" />
      ) : (
        <Wide>
          <table className="w-full text-base" aria-label="Chart of accounts">
            <thead>
              <tr>
                <th className="text-left">Expense account</th>
                <th className="text-left">Schedule E line</th>
              </tr>
            </thead>
            <tbody>
              {books.expense_accounts.map((account) => (
                <tr key={account.id} aria-label={account.name}>
                  <td>{account.name}</td>
                  <td className="text-ink-muted">{lineLabel(books, account.schedule_e_line)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </Wide>
      )}
      {adding && books && (
        <AddAccountPanel books={books} say={setSaid} refresh={reload} close={() => setAdding(false)} />
      )}
      <div className="mt-6"><ChargeTypesCard say={setSaid} /></div>
    </Shell>
  );
}

function AddAccountPanel({ books, say, refresh, close }: {
  books: Portfolio;
  say: (said: string) => void;
  refresh: () => void;
  close: () => void;
}) {
  const { error, record } = useRecord(refresh, say);
  const [name, setName] = useState("");
  const [line, setLine] = useState("");
  return (
    <SidePanel title="Add account" onClose={close}>
      <Form className="flex flex-col gap-3" onSubmit={async (event) => {
        event.preventDefault();
        if (await record("/api/setup/expense-accounts", { name, schedule_e_line: Number(line) }, () => `Added ${name.trim()}.`)) close();
      }}>
        <Field label="Expense account" id="expense-account-name" required maxLength={200} value={name}
          onChange={(e) => setName(e.target.value)} />
        <Select label="Schedule E line" id="expense-account-line" required value={line} onChange={(e) => setLine(e.target.value)}>
          <option value="">Choose a line</option>
          {books.schedule_e_lines.map((l) => <option key={l.line} value={l.line}>{l.line}: {l.label}</option>)}
        </Select>
        <div><Button type="submit">Add account</Button></div>
        <Notice tone="error">{error}</Notice>
      </Form>
    </SidePanel>
  );
}
