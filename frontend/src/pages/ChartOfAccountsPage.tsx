import { BookOpen, Tags } from "lucide-react";
import { useEffect, useState } from "react";
import type { Me } from "../api";
import { ApiError, api } from "../api";
import { Badge } from "../components/Badge";
import { Button, LinkButton } from "../components/Button";
import { EmptyState } from "../components/EmptyState";
import { Field } from "../components/Field";
import { Form } from "../components/Form";
import { Notice } from "../components/Notice";
import { RowMenu, type RowAction } from "../components/RowMenu";
import { Said } from "../components/Said";
import { Select } from "../components/Select";
import { Card, PageHeader, Shell } from "../components/Shell";
import { SidePanel } from "../components/SidePanel";
import { Wide } from "../components/Wide";
import {
  CHART_CSV, type Chart, type ChartAccount, PAYEES, type Payee, RANGES, accountPath, bySection,
  codeBody, codeOk,
} from "../chart";
import { STAFF_NAV } from "./staffNav";
import { useRecord } from "./useRecord";

// Settings → Chart of accounts (the founder's chart of accounts pass, 2026-11-05): every account
// of the company's chart by section and code: the control account of each kind of money the
// trust holds, the income statement's own lines, the company's charge types and its expense
// accounts (each on its Schedule E line). Staff give any account another code, rename or archive
// a charge type or an expense account, and say who keeps a charge type's fees, each in a side
// panel or a row's ⋯ menu; archiving asks first. Nothing here moves money or rewrites what was
// posted.
export function ChartOfAccountsPage({ me }: { me: Me }) {
  const [chart, setChart] = useState<Chart | null>(null);
  const [error, setError] = useState("");
  const [said, setSaid] = useState("");
  const [panel, setPanel] = useState<Panel | null>(null);
  function load() {
    api.get<Chart>("/api/setup/chart-of-accounts").then(setChart)
      .catch((failed) => setError(failed instanceof ApiError ? failed.detail : String(failed)));
  }
  useEffect(load, []);
  const { error: refused, record } = useRecord(load, setSaid);
  const close = () => setPanel(null);

  return (
    <Shell me={me} nav={STAFF_NAV}>
      <PageHeader title="Chart of accounts" actions={
        <>
          <LinkButton href={CHART_CSV}>Download CSV</LinkButton>
          <Button variant="secondary" onClick={() => setPanel({ kind: "add type" })}>Add charge type</Button>
          <Button onClick={() => setPanel({ kind: "add account" })}>Add expense account</Button>
        </>
      } />
      <Said>{said}</Said>
      <Notice tone="error">{refused}</Notice>
      {chart === null ? (
        <Notice tone={error ? "error" : "info"}>{error || "Loading"}</Notice>
      ) : chart.accounts.length === 0 ? (
        <EmptyState icon={BookOpen} title="No accounts yet" />
      ) : (
        <div className="flex flex-col gap-6">
          {bySection(chart.accounts).map((s) => (
            <Card key={s.section} title={s.label}>
              <Wide>
                <table className="w-full text-base" aria-label={s.label}>
                  <thead>
                    <tr>
                      <th className="text-left">Code</th>
                      <th className="text-left">Name</th>
                      <th className="text-left">Type</th>
                      {s.section === "expense" && <th className="text-left">Schedule E line</th>}
                      {s.section === "income" && <th className="text-left">Kept by</th>}
                      <th className="text-left">Status</th>
                      <th><span className="sr-only">Actions</span></th>
                    </tr>
                  </thead>
                  <tbody>
                    {s.accounts.map((account) => (
                      <tr key={`${account.line}${account.charge_type_id}${account.expense_account_id}`}
                        aria-label={account.name}>
                        <td>{account.code}</td>
                        <td>{account.name}</td>
                        <td className="text-ink-muted">{account.type}</td>
                        {s.section === "expense" && (
                          <td className="text-ink-muted">{lineLabel(chart, account.schedule_e_line)}</td>
                        )}
                        {s.section === "income" && (
                          <td className="text-ink-muted">{account.kept_by === "company" ? "Company" : "Owner"}</td>
                        )}
                        <td>
                          <Badge tone={account.archived ? "neutral" : "positive"}>
                            {account.archived ? "archived" : "active"}
                          </Badge>
                        </td>
                        <td className="text-right">
                          <RowMenu label={account.name} actions={actions(account, setPanel, record)} />
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </Wide>
              {s.section === "income" && !s.accounts.some((a) => a.charge_type_id) && (
                <EmptyState icon={Tags} title="No charge types yet" />
              )}
              {s.section === "expense" && !s.accounts.some((a) => a.expense_account_id) && (
                <EmptyState icon={BookOpen} title="No expense accounts yet" />
              )}
            </Card>
          ))}
        </div>
      )}
      {panel?.kind === "add account" && chart && (
        <AddAccountPanel chart={chart} say={setSaid} refresh={load} close={close} />
      )}
      {panel?.kind === "add type" && <AddTypePanel say={setSaid} refresh={load} close={close} />}
      {panel?.kind === "code" && <CodePanel account={panel.account} say={setSaid} refresh={load} close={close} />}
      {panel?.kind === "rename" && <RenamePanel account={panel.account} say={setSaid} refresh={load} close={close} />}
      {panel?.kind === "payee" && <PayeePanel account={panel.account} say={setSaid} refresh={load} close={close} />}
    </Shell>
  );
}

type Panel =
  | { kind: "add account" }
  | { kind: "add type" }
  | { kind: "code" | "rename" | "payee"; account: ChartAccount };

function lineLabel(chart: Chart, line: number | null): string {
  if (line === null) return "None";
  return `${line}: ${chart.schedule_e_lines.find((l) => l.line === line)?.label ?? ""}`;
}

// A row's ⋯ menu: another code for any account; a name, who keeps it and archiving for the
// company's own. Archiving asks first; bringing one back doesn't.
function actions(
  account: ChartAccount,
  open: (panel: Panel) => void,
  record: ReturnType<typeof useRecord>["record"],
): RowAction[] {
  const found: RowAction[] = [{ label: "Change code", onSelect: () => open({ kind: "code", account }) }];
  const path = accountPath(account);
  if (path === null) return found;
  found.push({ label: "Rename", onSelect: () => open({ kind: "rename", account }) });
  if (account.charge_type_id) {
    found.push({ label: "Change who keeps it", onSelect: () => open({ kind: "payee", account }) });
  }
  found.push(account.archived
    ? {
      label: "Bring back",
      onSelect: () => record(`${path}/archive`, { archived: false }, () => `${account.name} is back.`),
    }
    : {
      label: "Archive",
      danger: true,
      confirm: `Archive ${account.name}? It takes nothing new; its history and past report lines stay.`,
      onSelect: () => record(`${path}/archive`, { archived: true }, () => `${account.name} is archived.`),
    });
  return found;
}

type PanelProps = { say: (said: string) => void; refresh: () => void; close: () => void };

function AddAccountPanel({ chart, say, refresh, close }: PanelProps & { chart: Chart }) {
  const { error, record } = useRecord(refresh, say);
  const [name, setName] = useState("");
  const [line, setLine] = useState("");
  return (
    <SidePanel title="Add expense account" onClose={close}>
      <Form className="flex flex-col gap-3" onSubmit={async (event) => {
        event.preventDefault();
        if (await record("/api/setup/expense-accounts", { name, schedule_e_line: Number(line) },
          () => `Added ${name.trim()}.`)) close();
      }}>
        <Field label="Expense account" id="expense-account-name" required maxLength={200} value={name}
          onChange={(e) => setName(e.target.value)} />
        <Select label="Schedule E line" id="expense-account-line" required value={line} onChange={(e) => setLine(e.target.value)}>
          <option value="">Choose a line</option>
          {chart.schedule_e_lines.map((l) => <option key={l.line} value={l.line}>{l.line}: {l.label}</option>)}
        </Select>
        <p className="text-sm text-ink-muted">It takes the next free code of its line&apos;s block.</p>
        <div><Button type="submit">Add expense account</Button></div>
        <Notice tone="error">{error}</Notice>
      </Form>
    </SidePanel>
  );
}

function AddTypePanel({ say, refresh, close }: PanelProps) {
  const { error, record } = useRecord(refresh, say);
  const [name, setName] = useState("");
  const [payee, setPayee] = useState<Payee>("owner");
  return (
    <SidePanel title="Add charge type" onClose={close}>
      <Form className="flex flex-col gap-3" onSubmit={async (event) => {
        event.preventDefault();
        if (await record("/api/setup/charge-types", { name, payee }, () => `Added ${name.trim()}.`)) close();
      }}>
        <Field label="Charge type" id="charge-type-name" required maxLength={100} value={name}
          onChange={(e) => setName(e.target.value)} />
        <Select label="Who keeps its fees" id="charge-type-payee" value={payee}
          onChange={(e) => setPayee(e.target.value as Payee)}>
          {PAYEES.map((p) => <option key={p.value} value={p.value}>{p.label}</option>)}
        </Select>
        <div><Button type="submit">Add charge type</Button></div>
        <Notice tone="error">{error}</Notice>
      </Form>
    </SidePanel>
  );
}

function CodePanel({ account, say, refresh, close }: PanelProps & { account: ChartAccount }) {
  const { error, record } = useRecord(refresh, say);
  const [code, setCode] = useState(account.code);
  return (
    <SidePanel title="Change code" onClose={close}>
      <Form className="flex flex-col gap-3" onSubmit={async (event) => {
        event.preventDefault();
        if (await record("/api/setup/codes", codeBody(account, code.trim()),
          () => `${account.name} is ${code.trim()}.`)) close();
      }}>
        <p className="text-base text-ink">{account.name}</p>
        <Field label="Code" id="account-code" required inputMode="numeric" maxLength={4} value={code}
          onChange={(e) => setCode(e.target.value)} />
        <p className="text-sm text-ink-muted">Four digits from {RANGES[account.section]}, no other account&apos;s.</p>
        <div><Button type="submit" disabled={!codeOk(code)}>Save code</Button></div>
        <Notice tone="error">{error}</Notice>
      </Form>
    </SidePanel>
  );
}

function RenamePanel({ account, say, refresh, close }: PanelProps & { account: ChartAccount }) {
  const { error, record } = useRecord(refresh, say);
  const [name, setName] = useState(account.name);
  return (
    <SidePanel title="Rename" onClose={close}>
      <Form className="flex flex-col gap-3" onSubmit={async (event) => {
        event.preventDefault();
        if (await record(`${accountPath(account)}/name`, { name }, () => `Renamed to ${name.trim()}.`)) close();
      }}>
        <Field label="Name" id="account-name" required maxLength={account.charge_type_id ? 100 : 200}
          value={name} onChange={(e) => setName(e.target.value)} />
        <p className="text-sm text-ink-muted">Every report reads the new name, the past ones too.</p>
        <div><Button type="submit">Save name</Button></div>
        <Notice tone="error">{error}</Notice>
      </Form>
    </SidePanel>
  );
}

function PayeePanel({ account, say, refresh, close }: PanelProps & { account: ChartAccount }) {
  const { error, record } = useRecord(refresh, say);
  const [payee, setPayee] = useState<Payee>(account.kept_by ?? "owner");
  return (
    <SidePanel title="Who keeps its fees" onClose={close}>
      <Form className="flex flex-col gap-3" onSubmit={async (event) => {
        event.preventDefault();
        if (await record(`${accountPath(account)}/payee`, { payee },
          () => `${account.name}'s fees go to ${payee === "company" ? "the company" : "the owner"} from now on.`)) close();
      }}>
        <p className="text-base text-ink">{account.name}</p>
        <Select label="Who keeps its fees" id="account-payee" value={payee}
          onChange={(e) => setPayee(e.target.value as Payee)}>
          {PAYEES.map((p) => <option key={p.value} value={p.value}>{p.label}</option>)}
        </Select>
        <div>
          <Button type="submit" says="For fees charged from now on: payments pay them into that account. Fees charged before keep who they went to.">
            Save who keeps it
          </Button>
        </div>
        <Notice tone="error">{error}</Notice>
      </Form>
    </SidePanel>
  );
}
