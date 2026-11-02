import { BookOpen } from "lucide-react";
import { useEffect, useState } from "react";
import type { Me } from "../api";
import { ApiError, api } from "../api";
import { Button, LinkButton } from "../components/Button";
import { EmptyState } from "../components/EmptyState";
import { Field } from "../components/Field";
import { Form } from "../components/Form";
import { Notice } from "../components/Notice";
import { Said } from "../components/Said";
import { Card, PageHeader, Shell } from "../components/Shell";
import { SidePanel } from "../components/SidePanel";
import { Wide } from "../components/Wide";
import { presetDays } from "../reports";
import { STAFF_NAV } from "./staffNav";
import { useRecord } from "./useRecord";

export type QuickBooksAccount = {
  ledger_account_id: string;
  kind: string;
  bank: string;
  holder: string;
  default_name: string;
  name: string | null; // the company's own name for it; null: the PMS's
};

// The journal of the days as QuickBooks Desktop (IIF) or Online (CSV) imports it.
export function journalHref(from: string, to: string, format: "iif" | "csv"): string {
  return `/api/quickbooks/journal?${new URLSearchParams({ from, to, format })}`;
}

// Settings → QuickBooks (q117): the trust ledger's transfers over days as journal entries to
// import, and each ledger account's name in the company's QuickBooks (src/pms/quickbooks.py).
export function QuickBooksPage({ me }: { me: Me }) {
  const [accounts, setAccounts] = useState<QuickBooksAccount[] | null>(null);
  const [error, setError] = useState("");
  const [days, setDays] = useState(() => presetDays("last-month", new Date()));
  const [renaming, setRenaming] = useState<QuickBooksAccount | null>(null);
  const [said, setSaid] = useState("");
  function load() {
    api.get<QuickBooksAccount[]>("/api/quickbooks/accounts")
      .then(setAccounts)
      .catch((failed) => setError(failed instanceof ApiError ? failed.detail : String(failed)));
  }
  useEffect(load, []);
  return (
    <Shell me={me} nav={STAFF_NAV}>
      <PageHeader title="QuickBooks" />
      <Said>{said}</Said>
      <Card title="Export">
        <p className="mb-3 text-base text-ink-muted">
          Each transfer of the trust ledger in the days (UTC) becomes a general journal entry: it
          debits the account the money left and credits the account it went to, so each entry
          balances. Try the first import in a test company file.
        </p>
        <div className="flex flex-wrap items-end gap-3">
          <Field label="From" id="qb-from" type="date" value={days.from} onChange={(e) => setDays({ ...days, from: e.target.value })} />
          <Field label="To" id="qb-to" type="date" value={days.to} onChange={(e) => setDays({ ...days, to: e.target.value })} />
          <LinkButton href={journalHref(days.from, days.to, "iif")}>For QuickBooks Desktop (IIF)</LinkButton>
          <LinkButton href={journalHref(days.from, days.to, "csv")}>For QuickBooks Online (CSV)</LinkButton>
        </div>
      </Card>
      <Card title="Account names">
        <p className="mb-3 text-base text-ink-muted">
          Each ledger account goes into QuickBooks under the name below: the PMS&apos;s, until you
          give it the name of your own QuickBooks account. A colon puts it under a parent
          account, as QuickBooks writes a subaccount (Owner funds:Owner A).
        </p>
        {accounts === null ? (
          <Notice tone={error ? "error" : "info"}>{error || "Loading"}</Notice>
        ) : accounts.length === 0 ? (
          <EmptyState icon={BookOpen} title="No ledger accounts yet" />
        ) : (
          <Wide>
            <table className="w-full text-base" aria-label="Account names">
              <thead>
                <tr>
                  <th className="text-left">In QuickBooks</th>
                  <th className="text-left">Ledger account</th>
                  <th className="text-left">Trust bank account</th>
                  <th><span className="sr-only">Actions</span></th>
                </tr>
              </thead>
              <tbody>
                {accounts.map((account) => (
                  <tr key={account.ledger_account_id} aria-label={account.name ?? account.default_name}>
                    <td>{account.name ?? account.default_name}</td>
                    <td className="text-ink-muted">{account.holder}</td>
                    <td className="text-ink-muted">{account.bank}</td>
                    <td className="text-right">
                      <Button variant="secondary" size="sm" onClick={() => setRenaming(account)}
                        aria-label={`Rename ${account.name ?? account.default_name}`}>Rename</Button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </Wide>
        )}
      </Card>
      {renaming && (
        <RenamePanel account={renaming} say={setSaid} refresh={load} close={() => setRenaming(null)} />
      )}
    </Shell>
  );
}

function RenamePanel({ account, say, refresh, close }: {
  account: QuickBooksAccount;
  say: (said: string) => void;
  refresh: () => void;
  close: () => void;
}) {
  const { error, record } = useRecord(refresh, say);
  const [name, setName] = useState(account.name ?? account.default_name);
  const path = `/api/quickbooks/accounts/${encodeURIComponent(account.ledger_account_id)}`;
  return (
    <SidePanel title="Name in QuickBooks" onClose={close}>
      <Form className="flex flex-col gap-3" onSubmit={async (event) => {
        event.preventDefault();
        if (await record(path, { name }, () => `${account.holder} goes into QuickBooks as ${name.trim()}.`)) close();
      }}>
        <p className="text-base text-ink-muted">{account.holder} ({account.bank})</p>
        <Field label="Name in QuickBooks" id="qb-name" required maxLength={200} value={name}
          onChange={(e) => setName(e.target.value)} />
        <div className="flex flex-wrap gap-3">
          <Button type="submit" says="The export uses this name from now on; the old one stays in the history. Nothing is sent to QuickBooks.">
            Save
          </Button>
          {account.name !== null && (
            <Button type="button" variant="secondary" says="The export uses the PMS's name for it again. Nothing is sent to QuickBooks."
              onClick={async () => {
                if (await record(path, { name: null }, () => `${account.holder} goes into QuickBooks as ${account.default_name}.`)) close();
              }}>Use the PMS&apos;s name</Button>
          )}
        </div>
        <Notice tone="error">{error}</Notice>
      </Form>
    </SidePanel>
  );
}
