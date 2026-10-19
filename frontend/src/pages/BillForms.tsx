import { Plus, X } from "lucide-react";
import { useState } from "react";
import { Button } from "../components/Button";
import { useConfirm } from "../components/Confirm";
import { Field } from "../components/Field";
import { Form } from "../components/Form";
import { Notice } from "../components/Notice";
import { Select } from "../components/Select";
import type { ContentType } from "../documents";
import { MAX_BYTES, titleOf, toBase64, typeOf } from "../documents";
import { day, money, sumMoney, usDate } from "../money";
import type { Bill, PayoutMethod } from "../payables";
import { evenShares, linesNote } from "../payables";
import type { Portfolio } from "../setup";
import { expenseAccountLabel } from "../setup";
import { sharesLine } from "../rentals";
import type { Recorder } from "./useRecord";

// A bill's expense account lines as staff type them: an account and an amount each.
export type DraftLine = { account: string; amount: string };

// The lines of a bill split across expense accounts: each an account and an amount, a line
// added or taken away, and what they come to against the bill.
function ExpenseLines({ books, amount, lines, onChange }: {
  books: Portfolio;
  amount: string;
  lines: DraftLine[];
  onChange: (lines: DraftLine[]) => void;
}) {
  const set = (n: number, line: DraftLine) => onChange(lines.map((was, i) => (i === n ? line : was)));
  return (
    <fieldset className="flex flex-col gap-2">
      <legend className="mb-1 text-sm text-ink-muted">Split across expense accounts</legend>
      {lines.map((line, n) => (
        <div key={n} className="flex flex-wrap items-center gap-2">
          <select aria-label={`Expense account, line ${n + 1}`} required value={line.account}
            onChange={(e) => set(n, { ...line, account: e.target.value })}
            className="h-9 rounded-sm border border-line-strong bg-sunken px-2 text-sm text-ink">
            <option value="">Choose an account</option>
            {books.expense_accounts.map((a) => (
              <option key={a.id} value={a.id}>{expenseAccountLabel(a, books.schedule_e_lines)}</option>
            ))}
          </select>
          <input aria-label={`Amount, line ${n + 1}`} inputMode="decimal" required value={line.amount}
            onChange={(e) => set(n, { ...line, amount: e.target.value })}
            className="h-9 w-28 rounded-sm border border-line-strong bg-sunken px-2 text-right text-sm text-ink" />
          {lines.length > 2 && (
            <Button type="button" variant="ghost" size="sm" aria-label={`Remove line ${n + 1}`}
              onClick={() => onChange(lines.filter((_, i) => i !== n))}>
              <X size={14} />
            </Button>
          )}
        </div>
      ))}
      <div className="flex flex-wrap items-center gap-3">
        <Button type="button" variant="ghost" size="sm" onClick={() => onChange([...lines, { account: "", amount: "" }])}>
          <Plus size={14} /> Add line
        </Button>
        <span className="text-sm text-ink-muted" data-testid="lines-total">
          {linesNote(lines.map((l) => l.amount), amount)}
        </span>
      </div>
    </fieldset>
  );
}

// The body a bill's expense accounts are sent as: one account, or the lines.
export function linesBody(split: boolean, account: string, lines: DraftLine[]) {
  if (split) return { lines: lines.map((l) => ({ expense_account_id: l.account, amount: l.amount })) };
  return account ? { expense_account_id: account } : {};
}

const TWO_LINES: DraftLine[] = [{ account: "", amount: "" }, { account: "", amount: "" }];

// One vendor invoice shared across properties: each owner's property account's share, an account
// added or taken away, the total split evenly, and what the shares come to against the invoice.
function Shares({ books, amount, shares, onChange }: {
  books: Portfolio;
  amount: string;
  shares: DraftLine[];
  onChange: (shares: DraftLine[]) => void;
}) {
  const set = (n: number, share: DraftLine) => onChange(shares.map((was, i) => (i === n ? share : was)));
  const even = () => {
    const amounts = evenShares(amount, shares.length);
    onChange(shares.map((share, i) => ({ ...share, amount: amounts[i] ?? "" })));
  };
  return (
    <fieldset className="flex flex-col gap-2">
      <legend className="mb-1 text-sm text-ink-muted">Shares across properties</legend>
      {shares.map((share, n) => (
        <div key={n} className="flex flex-wrap items-center gap-2">
          <select aria-label={`Owner and property, share ${n + 1}`} required value={share.account}
            onChange={(e) => set(n, { ...share, account: e.target.value })}
            className="h-9 rounded-sm border border-line-strong bg-sunken px-2 text-sm text-ink">
            <option value="">Choose a property</option>
            {books.accounts.map((a) => <option key={a.account} value={a.account}>{a.owner}, {a.property}</option>)}
          </select>
          <input aria-label={`Amount, share ${n + 1}`} inputMode="decimal" required value={share.amount}
            onChange={(e) => set(n, { ...share, amount: e.target.value })}
            className="h-9 w-28 rounded-sm border border-line-strong bg-sunken px-2 text-right text-sm text-ink" />
          {shares.length > 2 && (
            <Button type="button" variant="ghost" size="sm" aria-label={`Remove share ${n + 1}`}
              onClick={() => onChange(shares.filter((_, i) => i !== n))}>
              <X size={14} />
            </Button>
          )}
        </div>
      ))}
      <div className="flex flex-wrap items-center gap-3">
        {shares.length < 20 && (
          <Button type="button" variant="ghost" size="sm" onClick={() => onChange([...shares, { account: "", amount: "" }])}>
            <Plus size={14} /> Add property
          </Button>
        )}
        <Button type="button" variant="ghost" size="sm" onClick={even}>Split evenly</Button>
        <span className="text-sm text-ink-muted" data-testid="shares-total">
          {linesNote(shares.map((s) => s.amount), amount).replace("Lines", "Shares")}
        </span>
      </div>
    </fieldset>
  );
}

// The percent of a bill the owner's terms let the company add as its markup, if any.
function markupOf(books: Portfolio, account: string): string | null {
  const percent = books.accounts.find((a) => a.account === account)?.agreement?.bill_markup_percent;
  return percent !== undefined && Number(percent) > 0 ? percent : null;
}

// The owners' accounts of the account's property by their ownership shares in force today
// (migration 0094), a bill split by ownership going to each; none without shares.
function ownersOf(books: Portfolio, account: string): Portfolio["accounts"] {
  const property = books.accounts.find((a) => a.account === account)?.property_id;
  return books.accounts.filter((a) => a.property_id === property && a.ownership_percent !== null);
}

type Invoice = { title: string; type: ContentType | null; size: number; base64: string };

// Where Record bill starts when a work order's Create bill opens it: its vendor, property, memo
// and the work order the bill names.
export type BillStart = { vendorId: string | null; propertyId: string; workOrderId: string; memo: string };

// Record bill: a vendor's bill against an owner's property account, in an expense account or
// split across several, maybe with the company's markup and the vendor's invoice kept as its
// receipt; or one invoice shared across properties, a bill for each one's share.
export function RecordBillForm({ books, record, start }: { books: Portfolio; record: Recorder; start?: BillStart }) {
  const [vendor, setVendor] = useState(start?.vendorId ?? books.vendors[0]?.id ?? "");
  const [account, setAccount] = useState(
    books.accounts.find((a) => a.property_id === start?.propertyId)?.account ?? books.accounts[0]?.account ?? "");
  const [reference, setReference] = useState("");
  const [dated, setDated] = useState(day(new Date()));
  const [due, setDue] = useState(day(new Date()));
  const [amount, setAmount] = useState("");
  const [memo, setMemo] = useState(start?.memo ?? "");
  const [expense, setExpense] = useState("");
  const [split, setSplit] = useState(false);
  const [lines, setLines] = useState<DraftLine[]>(TWO_LINES);
  const [invoice, setInvoice] = useState<Invoice | null>(null);
  const [shared, setShared] = useState(false);
  const [shares, setShares] = useState<DraftLine[]>(
    books.accounts.slice(0, 2).map((a) => ({ account: a.account, amount: "" })));
  const [markup, setMarkup] = useState(false);
  const [owned, setOwned] = useState(true);
  if (books.vendors.length === 0 || books.accounts.length === 0) {
    return <Notice tone="info">Add a vendor from Vendors and a property with an owner from Properties first.</Notice>;
  }
  const problem = invoice === null ? ""
    : invoice.type === null ? "An invoice is a PDF, a JPEG or a PNG."
      : invoice.size > MAX_BYTES ? "An invoice is at most 5 MB." : "";
  const sharesOff = shared && sumMoney(shares.map((s) => s.amount || "0")) !== sumMoney([amount || "0"]);
  // A property its owners own in shares: its bill split between them, unless staff untick it.
  const owners = shared || start ? [] : ownersOf(books, account);
  const byOwnership = owned && owners.length > 1;
  // The markup the owner's terms set: one owner's, or any share's when shared or split.
  const percent = shared || byOwnership ? null : markupOf(books, account);
  const canMarkUp = shared ? shares.some((s) => markupOf(books, s.account) !== null)
    : byOwnership ? owners.some((o) => markupOf(books, o.account) !== null) : percent !== null;
  const attached = invoice ? { invoice: { title: invoice.title, content_type: invoice.type, content_base64: invoice.base64 } } : {};
  return (
    <Form
      className="flex flex-col gap-3"
      onSubmit={(event) => {
        event.preventDefault();
        if (problem || sharesOff) return;
        const marked = canMarkUp && markup ? { markup: true } : {};
        if (shared) {
          const body = {
            vendor_id: vendor, reference, bill_date: dated, due_on: due, memo,
            shares: shares.map((s) => ({ account: s.account, amount: s.amount })),
            ...(expense ? { expense_account_id: expense } : {}),
            ...marked, ...attached,
          };
          void record("/api/payables/shared-bills", body,
            () => `Bill ${reference} is shared across ${shares.length} properties.`);
          return;
        }
        if (byOwnership) {
          const body = {
            vendor_id: vendor, reference, bill_date: dated, due_on: due, memo,
            by_ownership: { account, amount },
            ...(expense ? { expense_account_id: expense } : {}),
            ...marked, ...attached,
          };
          void record<{ bills: string[] }>("/api/payables/shared-bills", body,
            (done) => `Bill ${reference} is split between ${done.bills.length} owners by their shares.`);
          return;
        }
        const body = {
          vendor_id: vendor, account, reference, bill_date: dated, due_on: due, amount, memo,
          ...(start ? { work_order_id: start.workOrderId } : {}),
          ...linesBody(split, expense, lines),
          ...marked, ...attached,
        };
        void record("/api/payables/bills", body, () => `Bill ${reference} recorded.`);
      }}
    >
      <Select label="Vendor" id="bill-vendor" value={vendor} onChange={(e) => setVendor(e.target.value)}>
        {books.vendors.map((v) => <option key={v.id} value={v.id}>{v.name}</option>)}
      </Select>
      {!start && books.accounts.length > 1 && (
        <label className="flex items-center gap-2 text-sm text-ink">
          <input type="checkbox" checked={shared} onChange={(e) => setShared(e.target.checked)} />
          Share across properties
        </label>
      )}
      {!shared && (
        <Select label="Owner and property" id="bill-account" value={account} onChange={(e) => setAccount(e.target.value)}>
          {books.accounts.map((a) => <option key={a.account} value={a.account}>{a.owner}, {a.property}</option>)}
        </Select>
      )}
      {owners.length > 1 && (
        <label className="flex items-center gap-2 text-sm text-ink">
          <input type="checkbox" checked={owned} onChange={(e) => setOwned(e.target.checked)} />
          Split by ownership: {sharesLine(owners.map((o) => ({ owner: o.owner, percent: o.ownership_percent })))}
        </label>
      )}
      <Field label="Ref #" id="bill-reference" required maxLength={200} value={reference}
        onChange={(e) => setReference(e.target.value)} />
      <div className="flex flex-wrap gap-3">
        <Field label="Bill date" id="bill-date" type="date" required value={dated} onChange={(e) => setDated(e.target.value)} />
        <Field label="Due" id="bill-due" type="date" required value={due} onChange={(e) => setDue(e.target.value)} />
      </div>
      <Field label={shared ? "Invoice total" : "Amount"} id="bill-amount" inputMode="decimal" required value={amount}
        onChange={(e) => setAmount(e.target.value)} />
      {shared && <Shares books={books} amount={amount} shares={shares} onChange={setShares} />}
      <Field label="Memo" id="bill-memo" required maxLength={200} value={memo} onChange={(e) => setMemo(e.target.value)} />
      {canMarkUp && (
        <label className="flex items-center gap-2 text-sm text-ink">
          <input type="checkbox" checked={markup} onChange={(e) => setMarkup(e.target.checked)} />
          {percent !== null ? `Add the owner's markup (${percent}%)` : "Add each owner's markup, as their terms set"}
        </label>
      )}
      {books.expense_accounts.length > 0 && (shared || byOwnership) && (
        <Select label="Expense account" id="bill-expense" value={expense} onChange={(e) => setExpense(e.target.value)}>
          <option value="">None yet</option>
          {books.expense_accounts.map((a) => (
            <option key={a.id} value={a.id}>{expenseAccountLabel(a, books.schedule_e_lines)}</option>
          ))}
        </Select>
      )}
      {books.expense_accounts.length > 0 && !shared && !byOwnership && (
        <>
          <label className="flex items-center gap-2 text-sm text-ink">
            <input type="checkbox" checked={split} onChange={(e) => setSplit(e.target.checked)} />
            Split across expense accounts
          </label>
          {split ? (
            <ExpenseLines books={books} amount={amount} lines={lines} onChange={setLines} />
          ) : (
            <Select label="Expense account" id="bill-expense" value={expense} onChange={(e) => setExpense(e.target.value)}>
              <option value="">None yet</option>
              {books.expense_accounts.map((a) => (
                <option key={a.id} value={a.id}>{expenseAccountLabel(a, books.schedule_e_lines)}</option>
              ))}
            </Select>
          )}
        </>
      )}
      <label htmlFor="bill-invoice" className="flex flex-col gap-1.5 text-sm text-ink-muted">
        Invoice (PDF, JPEG or PNG)
        <input id="bill-invoice" type="file" accept=".pdf,.jpg,.jpeg,.png" className="text-sm text-ink"
          onChange={async (e) => {
            const picked = e.target.files?.[0];
            if (!picked) return setInvoice(null);
            const bytes = new Uint8Array(await picked.arrayBuffer());
            setInvoice({ title: titleOf(picked.name), type: typeOf(picked), size: picked.size, base64: toBase64(bytes) });
          }} />
      </label>
      <Notice tone="error">{problem}</Notice>
      <div><Button type="submit" disabled={problem !== "" || sharesOff}>Record bill</Button></div>
    </Form>
  );
}

// The owner's approval of a bill over their limit, as staff record it (by phone, say).
export function ApprovalForm({ bill, record }: { bill: Bill; record: Recorder }) {
  const [on, setOn] = useState(day(new Date()));
  const [by, setBy] = useState("");
  return (
    <Form className="flex flex-col gap-3" onSubmit={(event) => {
      event.preventDefault();
      void record(`/api/payables/bills/${bill.id}/approval`, { approved_on: on, approved_by: by },
        () => `The owner's approval of ${bill.reference} is recorded.`);
    }}>
      <p className="text-sm text-ink-muted">Over the owner's approval limit of {money(bill.approval_limit)}.</p>
      <Field label="Approved by" id={`approved-by-${bill.id}`} required maxLength={200} value={by}
        onChange={(e) => setBy(e.target.value)} />
      <Field label="Approved on" id={`approved-on-${bill.id}`} type="date" required value={on}
        onChange={(e) => setOn(e.target.value)} />
      <div><Button type="submit">Record approval</Button></div>
    </Form>
  );
}

// The owner's decline as they gave it to staff: the bill is never set aside or paid.
export function DeclineForm({ bill, record }: { bill: Bill; record: Recorder }) {
  const [on, setOn] = useState(day(new Date()));
  const [by, setBy] = useState("");
  const [why, setWhy] = useState("");
  return (
    <Form className="flex flex-col gap-3" onSubmit={(event) => {
      event.preventDefault();
      void record(`/api/payables/bills/${bill.id}/decline`, { declined_on: on, declined_by: by, reason: why },
        () => `The owner's decline of ${bill.reference} is recorded.`);
    }}>
      <Field label="Declined by" id={`declined-by-${bill.id}`} required maxLength={200} value={by}
        onChange={(e) => setBy(e.target.value)} />
      <Field label="Declined on" id={`declined-on-${bill.id}`} type="date" required value={on}
        onChange={(e) => setOn(e.target.value)} />
      <Field label="Why" id={`declined-why-${bill.id}`} required maxLength={1000} value={why}
        onChange={(e) => setWhy(e.target.value)} />
      <div><Button type="submit">Record decline</Button></div>
    </Form>
  );
}

// Set aside: the bill's amount moves from the owner's money to the vendor's, to pay it.
export function SetAsideForm({ bill, record }: { bill: Bill; record: Recorder }) {
  const [on, setOn] = useState(day(new Date()));
  return (
    <Form className="flex flex-col gap-3" onSubmit={(event) => {
      event.preventDefault();
      void record(`/api/payables/bills/${bill.id}/set-aside`, { on },
        () => `${money(bill.amount)} is set aside for ${bill.reference}.`);
    }}>
      <Field label="Set aside on" id={`set-aside-on-${bill.id}`} type="date" required value={on}
        onChange={(e) => setOn(e.target.value)} />
      <div><Button type="submit">Set aside</Button></div>
    </Form>
  );
}

// Pay: the trust bank account paid the vendor, by check or EFT. It sends money out of trust, so
// staff confirm the amount first. The PMS records it; it sends nothing.
export function PayForm({ bill, record }: { bill: Bill; record: Recorder }) {
  const [on, setOn] = useState(day(new Date()));
  const [method, setMethod] = useState<PayoutMethod>("check");
  const { ask, prompt } = useConfirm();
  return (
    <Form className="flex flex-col gap-3" onSubmit={(event) => {
      event.preventDefault();
      ask(`Pay ${money(bill.amount)} to ${bill.vendor} for ${bill.reference} by ${method} on ${usDate(on)}?`, () =>
        record(`/api/payables/bills/${bill.id}/pay`, { on, method }, () => `${bill.reference} is paid.`));
    }}>
      <Select label="Sent by" id={`pay-method-${bill.id}`} value={method}
        onChange={(e) => setMethod(e.target.value as PayoutMethod)}>
        <option value="check">Check</option>
        <option value="EFT">EFT</option>
      </Select>
      <Field label="Paid on" id={`pay-on-${bill.id}`} type="date" required value={on} onChange={(e) => setOn(e.target.value)} />
      <div><Button type="submit">Pay {money(bill.amount)}</Button></div>
      {prompt}
    </Form>
  );
}

// A bill's expense accounts changed: one account for the whole bill, or lines splitting it.
export function ExpenseAccountForm({ bill, books, current, record }: {
  bill: Bill;
  books: Portfolio;
  current: DraftLine[];
  record: Recorder;
}) {
  const [split, setSplit] = useState(current.length > 1);
  const [account, setAccount] = useState(current.length === 1 ? current[0]?.account ?? "" : "");
  const [lines, setLines] = useState<DraftLine[]>(current.length > 1 ? current : TWO_LINES);
  const total = sumMoney(lines.map((l) => l.amount || "0"));
  return (
    <Form className="flex flex-col gap-3" onSubmit={(event) => {
      event.preventDefault();
      void record(`/api/payables/bills/${bill.id}/expense-account`, linesBody(split, account, lines),
        () => (split ? `${bill.reference} is split across ${lines.length} expense accounts.` : `${bill.reference} is in one expense account.`));
    }}>
      <label className="flex items-center gap-2 text-sm text-ink">
        <input type="checkbox" checked={split} onChange={(e) => setSplit(e.target.checked)} />
        Split across expense accounts
      </label>
      {split ? (
        <ExpenseLines books={books} amount={bill.amount} lines={lines} onChange={setLines} />
      ) : (
        <Select label="Expense account" id={`expense-${bill.id}`} required value={account}
          onChange={(e) => setAccount(e.target.value)}>
          <option value="">Choose an account</option>
          {books.expense_accounts.map((a) => (
            <option key={a.id} value={a.id}>{expenseAccountLabel(a, books.schedule_e_lines)}</option>
          ))}
        </Select>
      )}
      <div>
        <Button type="submit" disabled={split && total !== sumMoney([bill.amount])}>Save</Button>
      </div>
    </Form>
  );
}
