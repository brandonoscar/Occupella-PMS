import { Receipt, Repeat } from "lucide-react";
import { useEffect, useState } from "react";
import type { Me } from "../api";
import { ApiError, api } from "../api";
import { Badge } from "../components/Badge";
import { Button, LinkButton } from "../components/Button";
import { EmptyState } from "../components/EmptyState";
import { Field } from "../components/Field";
import { Form } from "../components/Form";
import { ListBar } from "../components/ListBar";
import { Notice } from "../components/Notice";
import { Pager } from "../components/Pager";
import type { RowAction } from "../components/RowMenu";
import { RowMenu } from "../components/RowMenu";
import { Said } from "../components/Said";
import { Select } from "../components/Select";
import { PageHeader, Shell } from "../components/Shell";
import { SidePanel } from "../components/SidePanel";
import { Tabs } from "../components/Tabs";
import { Wide } from "../components/Wide";
import { latest } from "../latest";
import { day, money, usDate } from "../money";
import type { Bill, Recurring } from "../payables";
import { billBadge, enteredWords } from "../payables";
import type { Paged } from "../rentals";
import { navigate } from "../router";
import { scopeParams, useScope } from "../scope";
import type { Portfolio } from "../setup";
import { expenseAccountLabel } from "../setup";
import { ApprovalForm, DeclineForm, PayForm, RecordBillForm, SetAsideForm } from "./BillForms";
import { GoLink } from "./NeedsAttention";
import { STAFF_NAV } from "./staffNav";
import type { Recorder } from "./useRecord";
import { useRecord } from "./useRecord";

type Show = "open" | "paid" | "declined" | "recurring";
const TABS: { value: Show; label: string }[] = [
  { value: "open", label: "Unpaid" },
  { value: "paid", label: "Paid" },
  { value: "recurring", label: "Recurring" },
  { value: "declined", label: "Declined" },
];
const NONE: Record<Exclude<Show, "recurring">, string> = {
  open: "Nothing left to pay.",
  paid: "No bill is paid yet.",
  declined: "No bill was declined.",
};

// A side panel open on the page: Record bill, a bill's next step, or a recurring bill's.
type Panel =
  | { kind: "record" }
  | { kind: "approve" | "decline" | "set-aside" | "pay"; bill: Bill }
  | { kind: "add-recurring" }
  | { kind: "stop" | "recurring-account"; recurring: Recurring };

const TITLES: Record<Panel["kind"], string> = {
  record: "Record bill",
  approve: "Record approval",
  decline: "Record decline",
  "set-aside": "Set aside",
  pay: "Pay",
  "add-recurring": "Add recurring bill",
  stop: "Stop recurring bill",
  "recurring-account": "Expense account",
};

// Accounting → Bills (ADR 0006, Buildium's): Unpaid, Paid, Recurring and Declined, narrowed by
// property group, vendor and the day they are due by, a page at a time; the row opens the bill.
// Record bill and each bill's next step open a side panel; Pay bills pays several at once. Each
// month's recurring bills are entered by the nightly run. The PMS records what the bank did; it
// sends no payment itself.
export function BillsPage({ me }: { me: Me }) {
  const [show, setShow] = useState<Show>("open");
  const [scope, setScope] = useScope();
  const [vendor, setVendor] = useState("");
  const [dueBy, setDueBy] = useState("");
  const [number, setNumber] = useState(1);
  const [page, setPage] = useState<Paged<Bill> | null>(null);
  const [recurring, setRecurring] = useState<Recurring[] | null>(null);
  const [books, setBooks] = useState<Portfolio | null>(null);
  const [error, setError] = useState("");
  const [panel, setPanel] = useState<Panel | null>(null);
  const [runs, setRuns] = useState(0);
  const { said, error: refused, record } = useRecord(() => setRuns((n) => n + 1));
  const failed = (why: unknown) => setError(why instanceof ApiError ? why.detail : String(why));

  useEffect(() => {
    const run = latest();
    setError("");
    if (show === "recurring") {
      api.get<Recurring[]>("/api/payables/recurring").then(run.keep(setRecurring)).catch(run.keep(failed));
      return run.stop;
    }
    const query = new URLSearchParams({ show, page: String(number), ...scopeParams(scope) });
    if (vendor) query.set("vendor", vendor);
    if (dueBy) query.set("due_by", dueBy);
    api.get<Paged<Bill>>(`/api/payables/bills?${query}`).then(run.keep(setPage)).catch(run.keep(failed));
    return run.stop;
  }, [show, scope, vendor, dueBy, number, runs]);
  useEffect(() => {
    api.get<Portfolio>("/api/setup").then(setBooks).catch(failed);
  }, []);

  // A form in the side panel closes once what it recorded is recorded.
  const recordThenClose: Recorder = async (path, body, done) => {
    const recorded = await record(path, body, done);
    if (recorded) setPanel(null);
    return recorded;
  };
  const restart = <T,>(set: (value: T) => void) => (value: T) => { set(value); setNumber(1); };

  return (
    <Shell me={me} nav={STAFF_NAV}>
      <PageHeader title="Bills" actions={
        <div className="flex flex-wrap items-center gap-2">
          <LinkButton href="/pay-bills">Pay bills</LinkButton>
          <Button variant="secondary" onClick={() => setPanel({ kind: "record" })}>Record bill</Button>
        </div>
      } />
      <ListBar scope={scope} onScope={restart(setScope)}>
        <select aria-label="Vendor" value={vendor} onChange={(e) => restart(setVendor)(e.target.value)}
          className="h-9 rounded-sm border border-line-strong bg-canvas px-2 text-sm text-ink">
          <option value="">All vendors</option>
          {books?.vendors.map((v) => <option key={v.id} value={v.id}>{v.name}</option>)}
        </select>
        <label className="flex items-center gap-2 text-sm text-ink-muted">
          Due by
          <input type="date" aria-label="Due by" value={dueBy} onChange={(e) => restart(setDueBy)(e.target.value)}
            className="h-9 rounded-sm border border-line-strong bg-canvas px-2 text-sm text-ink" />
        </label>
      </ListBar>
      <div>
        <Tabs label="Bills" tabs={TABS} value={show} onChange={restart(setShow)} />
        <Said>{said}</Said>
        {panel === null && <Notice tone="error">{refused}</Notice>}
        {show === "recurring" ? (
          <RecurringTab recurring={recurring} error={error} books={books} onPanel={setPanel} record={record} />
        ) : page === null ? (
          <Notice tone={error ? "error" : "info"}>{error || "Loading"}</Notice>
        ) : page.items.length === 0 ? (
          <EmptyState icon={Receipt} title={NONE[show]} />
        ) : (
          <>
            <BillsTable bills={page.items} onPanel={setPanel} />
            <Pager page={page} onPage={setNumber} />
          </>
        )}
      </div>
      {panel !== null && (
        <SidePanel title={TITLES[panel.kind]} onClose={() => setPanel(null)}>
          {"bill" in panel && <p className="text-base text-ink">{panel.bill.vendor} {panel.bill.reference}, {money(panel.bill.amount)}</p>}
          {panel.kind === "record" && (books ? <RecordBillForm books={books} record={recordThenClose} /> : <Notice tone="info">Loading</Notice>)}
          {panel.kind === "approve" && <ApprovalForm bill={panel.bill} record={recordThenClose} />}
          {panel.kind === "decline" && <DeclineForm bill={panel.bill} record={recordThenClose} />}
          {panel.kind === "set-aside" && <SetAsideForm bill={panel.bill} record={recordThenClose} />}
          {panel.kind === "pay" && <PayForm bill={panel.bill} record={recordThenClose} />}
          {panel.kind === "add-recurring" && books && <NewRecurringForm books={books} record={recordThenClose} />}
          {panel.kind === "stop" && <StopForm recurring={panel.recurring} record={recordThenClose} />}
          {panel.kind === "recurring-account" && books && (
            <RecurringAccountForm recurring={panel.recurring} books={books} record={recordThenClose} />
          )}
          <Notice tone="error">{refused}</Notice>
        </SidePanel>
      )}
    </Shell>
  );
}

// A bill's next steps, in its row's ⋯ menu.
function stepsOf(bill: Bill, onPanel: (panel: Panel) => void): RowAction[] {
  if (bill.status === "needs approval") {
    return [
      { label: "Record approval", onSelect: () => onPanel({ kind: "approve", bill }) },
      { label: "Record decline", onSelect: () => onPanel({ kind: "decline", bill }) },
    ];
  }
  if (bill.status === "to set aside") return [{ label: "Set aside", onSelect: () => onPanel({ kind: "set-aside", bill }) }];
  if (bill.status === "set aside") return [{ label: "Pay", onSelect: () => onPanel({ kind: "pay", bill }) }];
  return [];
}

function BillsTable({ bills, onPanel }: { bills: Bill[]; onPanel: (panel: Panel) => void }) {
  return (
    <Wide>
      <table className="w-full text-base">
        <thead>
          <tr>
            <th className="text-left">Due</th>
            <th className="text-left">Vendor</th>
            <th className="text-left">Property</th>
            <th className="text-left">Memo</th>
            <th className="text-left">Ref #</th>
            <th className="text-right">Amount</th>
            <th className="text-left">Status</th>
            <th><span className="sr-only">Actions</span></th>
          </tr>
        </thead>
        <tbody>
          {bills.map((bill) => {
            const badge = billBadge(bill);
            const steps = stepsOf(bill, onPanel);
            return (
              <tr key={bill.id} aria-label={`${bill.vendor} ${bill.reference}`} className="cursor-pointer hover:bg-raised"
                onClick={() => navigate(`/bills/${bill.id}`)}>
                <td className="whitespace-nowrap">{usDate(bill.due_on)}</td>
                <td>{bill.vendor}</td>
                <td>{bill.property}</td>
                <td className="text-ink-muted">{bill.memo}</td>
                <td><GoLink href={`/bills/${bill.id}`} className="font-medium text-iris hover:text-iris-hover hover:underline">{bill.reference}</GoLink></td>
                <td className="text-right">{money(bill.amount)}</td>
                <td><Badge tone={badge.tone} data-testid="status">{badge.word}</Badge></td>
                <td className="text-right" onClick={(event) => event.stopPropagation()}>
                  {steps.length > 0 && <RowMenu label={`${bill.vendor} ${bill.reference}`} actions={steps} />}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </Wide>
  );
}

// Recurring: each bill that comes every month, entered each month by the nightly run under its
// prefix and the month; Add recurring bill, and Stop or a new expense account in its ⋯ menu.
// Enter bills due now enters what the nightly run would enter tonight, as Run rent now does rent.
function RecurringTab({ recurring, error, books, onPanel, record }: {
  recurring: Recurring[] | null;
  error: string;
  books: Portfolio | null;
  onPanel: (panel: Panel) => void;
  record: Recorder;
}) {
  return (
    <div className="flex flex-col gap-4">
      <div className="flex flex-wrap items-center gap-2">
        {books && books.vendors.length > 0 && books.accounts.length > 0 && (
          <Button variant="secondary" onClick={() => onPanel({ kind: "add-recurring" })}>Add recurring bill</Button>
        )}
        {recurring !== null && recurring.length > 0 && (
          <Button variant="secondary" onClick={() => {
            void record<{ entered: number; emails: number }>("/api/payables/recurring/enter", {}, enteredWords);
          }}>
            Enter bills due now
          </Button>
        )}
      </div>
      {recurring === null ? (
        <Notice tone={error ? "error" : "info"}>{error || "Loading"}</Notice>
      ) : recurring.length === 0 ? (
        <EmptyState icon={Repeat} title="No recurring bills yet." />
      ) : (
        <Wide>
          <table className="w-full text-base">
            <thead>
              <tr>
                <th className="text-left">Vendor</th>
                <th className="text-left">Owner and property</th>
                <th className="text-left">Ref # prefix</th>
                <th className="text-right">Amount</th>
                <th className="text-left">Day</th>
                <th className="text-left">From</th>
                <th className="text-left">Until</th>
                <th className="text-left">Expense account</th>
                <th><span className="sr-only">Actions</span></th>
              </tr>
            </thead>
            <tbody>
              {recurring.map((r) => {
                const actions: RowAction[] = [];
                if (books && books.expense_accounts.length > 0) {
                  actions.push({ label: "Change expense account", onSelect: () => onPanel({ kind: "recurring-account", recurring: r }) });
                }
                if (r.ends_on === null) actions.push({ label: "Stop it", onSelect: () => onPanel({ kind: "stop", recurring: r }) });
                return (
                  <tr key={r.id} aria-label={`${r.vendor} ${r.reference_prefix}`}>
                    <td>{r.vendor}</td>
                    <td>{r.owner}, {r.property}</td>
                    <td>{r.reference_prefix}</td>
                    <td className="text-right">{money(r.amount)}</td>
                    <td>{r.day_of_month}</td>
                    <td className="whitespace-nowrap">{usDate(r.starts_on)}</td>
                    <td className="whitespace-nowrap">{r.ends_on ? usDate(r.ends_on) : ""}</td>
                    <td className="text-ink-muted">{r.expense_account ?? ""}</td>
                    <td className="text-right">
                      {actions.length > 0 && <RowMenu label={`${r.vendor} ${r.reference_prefix}`} actions={actions} />}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </Wide>
      )}
    </div>
  );
}

function NewRecurringForm({ books, record }: { books: Portfolio; record: Recorder }) {
  const [vendor, setVendor] = useState(books.vendors[0]?.id ?? "");
  const [account, setAccount] = useState(books.accounts[0]?.account ?? "");
  const [prefix, setPrefix] = useState("");
  const [amount, setAmount] = useState("");
  const [memo, setMemo] = useState("");
  const [dayOfMonth, setDayOfMonth] = useState("1");
  const [starts, setStarts] = useState(day(new Date()));
  const [expense, setExpense] = useState("");
  return (
    <Form className="flex flex-col gap-3" onSubmit={(event) => {
      event.preventDefault();
      const body = {
        vendor_id: vendor, account, reference_prefix: prefix, amount, memo,
        day_of_month: Number(dayOfMonth), starts_on: starts,
        ...(expense ? { expense_account_id: expense } : {}),
      };
      void record("/api/payables/recurring", body, () => `${prefix} is entered each month.`);
    }}>
      <Select label="Vendor" id="recurring-vendor" value={vendor} onChange={(e) => setVendor(e.target.value)}>
        {books.vendors.map((v) => <option key={v.id} value={v.id}>{v.name}</option>)}
      </Select>
      <Select label="Owner and property" id="recurring-account" value={account} onChange={(e) => setAccount(e.target.value)}>
        {books.accounts.map((a) => <option key={a.account} value={a.account}>{a.owner}, {a.property}</option>)}
      </Select>
      <Field label="Ref # prefix" id="recurring-prefix" required maxLength={100} value={prefix}
        onChange={(e) => setPrefix(e.target.value)} />
      <Field label="Monthly amount" id="recurring-amount" inputMode="decimal" required value={amount}
        onChange={(e) => setAmount(e.target.value)} />
      <Field label="Memo" id="recurring-memo" required maxLength={200} value={memo} onChange={(e) => setMemo(e.target.value)} />
      <Field label="Day of the month" id="recurring-day" type="number" min={1} max={28} required value={dayOfMonth}
        onChange={(e) => setDayOfMonth(e.target.value)} />
      <Field label="First day" id="recurring-starts" type="date" required value={starts} onChange={(e) => setStarts(e.target.value)} />
      {books.expense_accounts.length > 0 && (
        <Select label="Expense account" id="recurring-expense" value={expense} onChange={(e) => setExpense(e.target.value)}>
          <option value="">None yet</option>
          {books.expense_accounts.map((a) => (
            <option key={a.id} value={a.id}>{expenseAccountLabel(a, books.schedule_e_lines)}</option>
          ))}
        </Select>
      )}
      <div><Button type="submit">Add recurring bill</Button></div>
    </Form>
  );
}

// Stops a recurring bill after its last day; months before it stay entered.
function StopForm({ recurring, record }: { recurring: Recurring; record: Recorder }) {
  const [on, setOn] = useState(day(new Date()));
  return (
    <Form className="flex flex-col gap-3" onSubmit={(event) => {
      event.preventDefault();
      void record(`/api/payables/recurring/${recurring.id}/end`, { ends_on: on },
        () => `${recurring.reference_prefix} stops after ${usDate(on)}.`);
    }}>
      <p className="text-base text-ink">{recurring.vendor}, {recurring.reference_prefix}</p>
      <Field label="Last day" id={`recurring-end-${recurring.id}`} type="date" required value={on}
        onChange={(e) => setOn(e.target.value)} />
      <div><Button type="submit" variant="danger">Stop it</Button></div>
    </Form>
  );
}

// The expense account each month's bill is entered in from now on; months entered keep theirs.
function RecurringAccountForm({ recurring, books, record }: { recurring: Recurring; books: Portfolio; record: Recorder }) {
  const [chosen, setChosen] = useState(recurring.expense_account_id ?? "");
  const name = books.expense_accounts.find((a) => a.id === chosen)?.name ?? "";
  return (
    <Form className="flex flex-col gap-3" onSubmit={(event) => {
      event.preventDefault();
      void record(`/api/payables/recurring/${recurring.id}/expense-account`, { expense_account_id: chosen },
        () => `${recurring.reference_prefix} is entered in ${name} from now on.`);
    }}>
      <p className="text-base text-ink">{recurring.vendor}, {recurring.reference_prefix}</p>
      <Select label="Expense account" id={`recurring-expense-${recurring.id}`} required value={chosen}
        onChange={(e) => setChosen(e.target.value)}>
        <option value="">Choose an account</option>
        {books.expense_accounts.map((a) => (
          <option key={a.id} value={a.id}>{expenseAccountLabel(a, books.schedule_e_lines)}</option>
        ))}
      </Select>
      <div><Button type="submit">Save</Button></div>
    </Form>
  );
}
