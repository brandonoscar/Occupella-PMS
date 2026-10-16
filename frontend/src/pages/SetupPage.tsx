import { useEffect, useState } from "react";
import type { Me } from "../api";
import { ApiError, api } from "../api";
import { Button } from "../components/Button";
import { Field } from "../components/Field";
import { Notice } from "../components/Notice";
import { Select } from "../components/Select";
import { Card, PageHeader, Shell } from "../components/Shell";
import type { BankKind, Named, Portfolio } from "../setup";
import { expenseAccountLabel } from "../setup";
import type { CardProps } from "./useAdd";
import { useAdd } from "./useAdd";
import { kindLabel } from "../setup";
import { LeasesCard } from "./LeasesCard";
import { PropertiesCard } from "./PropertiesCard";
import { STAFF_NAV } from "./staffNav";
import { TermsCard } from "./TermsCard";

// Staff set up the PMC's books: trust bank accounts, owners and vendors, expense accounts,
// properties with their units, owners and late fee terms, management terms, and tenants with
// leases already in place.
export function SetupPage({ me }: { me: Me }) {
  const [books, setBooks] = useState<Portfolio | null>(null);
  const [error, setError] = useState("");

  function refresh() {
    api
      .get<Portfolio>("/api/setup")
      .then(setBooks)
      .catch((failed) => setError(failed instanceof ApiError ? failed.detail : String(failed)));
  }
  useEffect(refresh, []);

  return (
    <Shell me={me} nav={STAFF_NAV}>
      <PageHeader title="Company setup" />
      {books === null ? (
        <Notice tone={error ? "error" : "info"}>{error || "Loading"}</Notice>
      ) : (
        <>
          <BanksCard books={books} refresh={refresh} />
          <PeopleCard books={books} refresh={refresh} />
          <ExpenseAccountsCard books={books} refresh={refresh} />
          <PropertiesCard books={books} refresh={refresh} />
          <TermsCard books={books} refresh={refresh} />
          <LeasesCard books={books} refresh={refresh} />
        </>
      )}
    </Shell>
  );
}

function BanksCard({ books, refresh }: CardProps) {
  const { error, add } = useAdd(refresh);
  const [kind, setKind] = useState<BankKind>("operating");
  const [name, setName] = useState("");
  return (
    <Card title="Trust bank accounts">
      <ul className="mb-4 flex flex-col gap-1">
        {books.bank_accounts.map((bank) => (
          <li key={bank.id}>
            {bank.name} <span className="text-sm text-ink-subtle">({kindLabel(bank.kind)})</span>
          </li>
        ))}
      </ul>
      <form
        className="flex flex-wrap items-end gap-3"
        onSubmit={async (event) => {
          event.preventDefault();
          if (await add("bank-accounts", { kind, name })) setName("");
        }}
      >
        <Select label="Kind" id="bank-kind" value={kind}
          onChange={(e) => setKind(e.target.value as BankKind)}>
          <option value="operating">Operating</option>
          <option value="security_deposit">Security deposit</option>
        </Select>
        <Field label="Bank account name" id="bank-name" required maxLength={200} value={name}
          onChange={(e) => setName(e.target.value)} />
        <Button type="submit" variant="secondary">Add bank account</Button>
      </form>
      <Notice tone="error">{error}</Notice>
    </Card>
  );
}

function NameList({
  title,
  path,
  label,
  people,
  refresh,
}: {
  title: string;
  path: string;
  label: string;
  people: Named[];
  refresh: () => void;
}) {
  const { error, add } = useAdd(refresh);
  const [name, setName] = useState("");
  return (
    <section aria-label={title} className="flex flex-col gap-3">
      <h3 className="text-md font-medium">{title}</h3>
      <ul className="flex flex-col gap-1">
        {people.map((person) => (
          <li key={person.id}>{person.name}</li>
        ))}
      </ul>
      <form
        className="flex flex-wrap items-end gap-3"
        onSubmit={async (event) => {
          event.preventDefault();
          if (await add(path, { name })) setName("");
        }}
      >
        <Field label={label} id={`${path}-name`} required maxLength={200} value={name}
          onChange={(e) => setName(e.target.value)} />
        <Button type="submit" variant="secondary">Add</Button>
      </form>
      <Notice tone="error">{error}</Notice>
    </section>
  );
}

function PeopleCard({ books, refresh }: CardProps) {
  return (
    <Card title="Owners and vendors">
      <div className="grid gap-8 sm:grid-cols-2">
        <NameList title="Owners" path="owners" label="Owner's name" people={books.owners}
          refresh={refresh} />
        <NameList title="Vendors" path="vendors" label="Vendor's name" people={books.vendors}
          refresh={refresh} />
      </div>
    </Card>
  );
}

// The accounts bills go in, each on its line of Schedule E, so an owner's income statement
// breaks expenses out the way their accountant reports them.
function ExpenseAccountsCard({ books, refresh }: CardProps) {
  const { error, add } = useAdd(refresh);
  const [name, setName] = useState("");
  const [line, setLine] = useState("");
  return (
    <Card title="Expense accounts">
      {books.expense_accounts.length === 0 ? (
        <Notice tone="info">No expense accounts yet. Bills are put in one to show on Schedule E.</Notice>
      ) : (
        <ul className="mb-4 flex flex-col gap-1">
          {books.expense_accounts.map((account) => (
            <li key={account.id}>{expenseAccountLabel(account, books.schedule_e_lines)}</li>
          ))}
        </ul>
      )}
      <form
        className="flex flex-wrap items-end gap-3"
        onSubmit={async (event) => {
          event.preventDefault();
          if (await add("expense-accounts", { name, schedule_e_line: Number(line) })) {
            setName("");
            setLine("");
          }
        }}
      >
        <Field label="Expense account" id="expense-account-name" required maxLength={200}
          value={name} onChange={(e) => setName(e.target.value)} />
        <Select label="Schedule E line" id="expense-account-line" required value={line}
          onChange={(e) => setLine(e.target.value)}>
          <option value="">Choose a line</option>
          {books.schedule_e_lines.map((l) => (
            <option key={l.line} value={l.line}>
              {l.line}: {l.label}
            </option>
          ))}
        </Select>
        <Button type="submit" variant="secondary">Add expense account</Button>
      </form>
      <Notice tone="error">{error}</Notice>
    </Card>
  );
}
