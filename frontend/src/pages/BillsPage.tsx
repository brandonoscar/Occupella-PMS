import { useEffect, useState } from "react";
import type { Me } from "../api";
import { ApiError, api } from "../api";
import { Button } from "../components/Button";
import { Field } from "../components/Field";
import { Notice } from "../components/Notice";
import { Select } from "../components/Select";
import { Card, Shell } from "../components/Shell";
import { day, money } from "../money";
import type { Bill, Recurring } from "../payables";
import { nextStep, recurringLabel } from "../payables";
import type { Portfolio } from "../setup";
import { STAFF_NAV } from "./staffNav";
import type { Recorder } from "./useRecord";
import { useRecord } from "./useRecord";

type Show = "open" | "all";

// Staff's vendor bills: enter one against an owner's property account, record the owner's
// approval (or decline) when it is over their limit, unless the owner decided in their portal,
// set it aside from the owner's money and record that the trust bank account paid it. Recurring bills are entered each month. The PMS records what
// the bank did; it sends no payment itself.
export function BillsPage({ me }: { me: Me }) {
  const [show, setShow] = useState<Show>("open");
  const [bills, setBills] = useState<Bill[] | null>(null);
  const [recurring, setRecurring] = useState<Recurring[]>([]);
  const [books, setBooks] = useState<Portfolio | null>(null);
  const [error, setError] = useState("");
  const failed = (why: unknown) => setError(why instanceof ApiError ? why.detail : String(why));

  function refresh() {
    api.get<Bill[]>(`/api/payables/bills?show=${show}`).then(setBills).catch(failed);
    api.get<Recurring[]>("/api/payables/recurring").then(setRecurring).catch(failed);
  }
  useEffect(refresh, [show]);
  useEffect(() => {
    api.get<Portfolio>("/api/setup").then(setBooks).catch(failed);
  }, []);

  return (
    <Shell me={me} nav={STAFF_NAV}>
      <h1 className="font-display text-xl font-medium">Bills</h1>
      {books && <EnterBillCard books={books} refresh={refresh} />}
      <Card title="Vendor bills">
        <div className="mb-4 max-w-48">
          <Select label="Show" id="bills-show" value={show}
            onChange={(e) => setShow(e.target.value as Show)}>
            <option value="open">Still to pay</option>
            <option value="all">All bills</option>
          </Select>
        </div>
        {bills === null ? (
          <Notice tone={error ? "error" : "info"}>{error || "Loading"}</Notice>
        ) : bills.length === 0 ? (
          <Notice tone="info">{show === "open" ? "Nothing left to pay: every bill is paid or declined." : "No bills yet."}</Notice>
        ) : (
          <ul className="flex flex-col divide-y divide-line">
            {bills.map((bill) => (
              <BillRow key={bill.id} bill={bill} refresh={refresh} />
            ))}
          </ul>
        )}
      </Card>
      {books && <RecurringCard books={books} recurring={recurring} refresh={refresh} />}
    </Shell>
  );
}

function EnterBillCard({ books, refresh }: { books: Portfolio; refresh: () => void }) {
  const { said, error, record } = useRecord(refresh);
  const [vendor, setVendor] = useState(books.vendors[0]?.id ?? "");
  const [account, setAccount] = useState(books.accounts[0]?.account ?? "");
  const [reference, setReference] = useState("");
  const [dated, setDated] = useState(day(new Date()));
  const [due, setDue] = useState(day(new Date()));
  const [amount, setAmount] = useState("");
  const [memo, setMemo] = useState("");
  if (books.vendors.length === 0 || books.accounts.length === 0) {
    return (
      <Card title="Enter a bill">
        <Notice tone="info">Add a vendor and a property with an owner in Setup first.</Notice>
      </Card>
    );
  }
  return (
    <Card title="Enter a bill">
      <form
        className="flex flex-wrap items-end gap-3"
        onSubmit={async (event) => {
          event.preventDefault();
          const body = {
            vendor_id: vendor, account, reference, bill_date: dated, due_on: due, amount, memo,
          };
          if (await record("/api/payables/bills", body, () => `Bill ${reference} entered.`)) {
            setReference("");
            setAmount("");
            setMemo("");
          }
        }}
      >
        <Select label="Vendor" id="bill-vendor" value={vendor} onChange={(e) => setVendor(e.target.value)}>
          {books.vendors.map((v) => (
            <option key={v.id} value={v.id}>
              {v.name}
            </option>
          ))}
        </Select>
        <Select label="Owner and property" id="bill-account" value={account}
          onChange={(e) => setAccount(e.target.value)}>
          {books.accounts.map((a) => (
            <option key={a.account} value={a.account}>
              {a.owner}, {a.property}
            </option>
          ))}
        </Select>
        <Field label="Vendor's reference" id="bill-reference" required maxLength={200}
          value={reference} onChange={(e) => setReference(e.target.value)} />
        <Field label="Bill date" id="bill-date" type="date" required value={dated}
          onChange={(e) => setDated(e.target.value)} />
        <Field label="Due on" id="bill-due" type="date" required value={due}
          onChange={(e) => setDue(e.target.value)} />
        <Field label="Amount" id="bill-amount" inputMode="decimal" required value={amount}
          onChange={(e) => setAmount(e.target.value)} />
        <Field label="What for" id="bill-memo" required maxLength={200} value={memo}
          onChange={(e) => setMemo(e.target.value)} />
        <Button type="submit">Enter bill</Button>
      </form>
      <Notice tone="info">{said}</Notice>
      <Notice tone="error">{error}</Notice>
    </Card>
  );
}

function BillRow({ bill, refresh }: { bill: Bill; refresh: () => void }) {
  const { said, error, record } = useRecord(refresh);
  const step = nextStep(bill.status);
  return (
    <li className="flex flex-col gap-2 py-4" aria-label={`${bill.vendor} ${bill.reference}`}>
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <p>
          <span className="font-medium">{bill.vendor} {bill.reference}</span>
          <span className="text-ink-muted">
            {" "}· {bill.owner}, {bill.property} · {bill.memo} · dated {bill.bill_date}, due {bill.due_on}
          </span>
        </p>
        <p>
          <span className="font-mono">{money(bill.amount)}</span>{" "}
          <span className="text-sm text-ink-muted" data-testid="status">{bill.status}</span>
        </p>
      </div>
      {bill.approved_by && (
        <p className="text-sm text-ink-subtle">Approved by {bill.approved_by}.</p>
      )}
      {bill.declined_by && (
        <p className="text-sm text-ink-subtle">
          Declined by {bill.declined_by}: {bill.decline_reason}. It is never paid from the owner's money.
        </p>
      )}
      {step === "Record approval" ? (
        <>
          <ApprovalForm bill={bill} record={record} />
          <DeclineForm bill={bill} record={record} />
        </>
      ) : step ? (
        <StepForm bill={bill} step={step} record={record} />
      ) : null}
      <Notice tone="info">{said}</Notice>
      <Notice tone="error">{error}</Notice>
    </li>
  );
}

function ApprovalForm({ bill, record }: { bill: Bill; record: Recorder }) {
  const [on, setOn] = useState(day(new Date()));
  const [by, setBy] = useState("");
  return (
    <form
      className="flex flex-wrap items-end gap-3"
      onSubmit={(event) => {
        event.preventDefault();
        record(`/api/payables/bills/${bill.id}/approval`, { approved_on: on, approved_by: by },
          () => `The owner's approval of ${bill.reference} is recorded.`);
      }}
    >
      <p className="w-full text-sm text-ink-muted">
        Over the owner's approval limit of {money(bill.approval_limit)}.
      </p>
      <Field label="Approved by" id={`approved-by-${bill.id}`} required maxLength={200} value={by}
        onChange={(e) => setBy(e.target.value)} />
      <Field label="Approved on" id={`approved-on-${bill.id}`} type="date" required value={on}
        onChange={(e) => setOn(e.target.value)} />
      <Button type="submit" variant="secondary">Record approval</Button>
    </form>
  );
}

// The owner's decline as they gave it to staff (by phone, say), behind a button: most owners
// approve, many in their portal.
function DeclineForm({ bill, record }: { bill: Bill; record: Recorder }) {
  const [open, setOpen] = useState(false);
  const [on, setOn] = useState(day(new Date()));
  const [by, setBy] = useState("");
  const [why, setWhy] = useState("");
  if (!open) {
    return (
      <div>
        <Button variant="secondary" onClick={() => setOpen(true)}>Record a decline instead</Button>
      </div>
    );
  }
  return (
    <form
      className="flex flex-wrap items-end gap-3"
      onSubmit={(event) => {
        event.preventDefault();
        record(`/api/payables/bills/${bill.id}/decline`,
          { declined_on: on, declined_by: by, reason: why },
          () => `The owner's decline of ${bill.reference} is recorded.`);
      }}
    >
      <Field label="Declined by" id={`declined-by-${bill.id}`} required maxLength={200} value={by}
        onChange={(e) => setBy(e.target.value)} />
      <Field label="Declined on" id={`declined-on-${bill.id}`} type="date" required value={on}
        onChange={(e) => setOn(e.target.value)} />
      <Field label="Why" id={`declined-why-${bill.id}`} required maxLength={1000} value={why}
        onChange={(e) => setWhy(e.target.value)} />
      <Button type="submit" variant="secondary">Record decline</Button>
    </form>
  );
}

function StepForm({ bill, step, record }: { bill: Bill; step: string; record: Recorder }) {
  const [on, setOn] = useState(day(new Date()));
  const path = step === "Pay" ? "pay" : "set-aside";
  const done = step === "Pay"
    ? `${bill.reference} is paid.`
    : `${money(bill.amount)} is set aside for ${bill.reference}.`;
  return (
    <form
      className="flex flex-wrap items-end gap-3"
      onSubmit={(event) => {
        event.preventDefault();
        record(`/api/payables/bills/${bill.id}/${path}`, { on }, () => done);
      }}
    >
      <Field label={step === "Pay" ? "Paid on" : "Set aside on"} id={`${path}-on-${bill.id}`}
        type="date" required value={on} onChange={(e) => setOn(e.target.value)} />
      <Button type="submit" variant="secondary">{step}</Button>
    </form>
  );
}

function RecurringCard({
  books,
  recurring,
  refresh,
}: {
  books: Portfolio;
  recurring: Recurring[];
  refresh: () => void;
}) {
  const { said, error, record } = useRecord(refresh);
  const [through, setThrough] = useState(day(new Date()));
  return (
    <Card title="Recurring bills">
      {recurring.length === 0 ? (
        <Notice tone="info">No recurring bills yet.</Notice>
      ) : (
        <ul className="mb-4 flex flex-col gap-2">
          {recurring.map((bill) => (
            <RecurringLine key={bill.id} bill={bill} record={record} />
          ))}
        </ul>
      )}
      <form
        className="mb-4 flex items-end gap-3"
        onSubmit={(event) => {
          event.preventDefault();
          record<{ entered: number }>("/api/payables/recurring/enter", { on: through },
            (done) => `${done.entered} bill${done.entered === 1 ? "" : "s"} entered.`);
        }}
      >
        <Field label="Enter bills due through" id="recurring-through" type="date" required
          value={through} onChange={(e) => setThrough(e.target.value)} />
        <Button type="submit" variant="secondary">Enter recurring bills</Button>
      </form>
      {books.vendors.length > 0 && books.accounts.length > 0 && (
        <NewRecurringForm books={books} record={record} />
      )}
      <Notice tone="info">{said}</Notice>
      <Notice tone="error">{error}</Notice>
    </Card>
  );
}

function RecurringLine({ bill, record }: { bill: Recurring; record: Recorder }) {
  const [on, setOn] = useState(day(new Date()));
  return (
    <li className="flex flex-wrap items-center gap-3 text-sm">
      <span>{recurringLabel(bill)}</span>
      {bill.ends_on === null && (
        <form
          className="flex items-center gap-2"
          onSubmit={(event) => {
            event.preventDefault();
            record(`/api/payables/recurring/${bill.id}/end`, { ends_on: on },
              () => `${bill.reference_prefix} stops after ${on}.`);
          }}
        >
          <input type="date" aria-label={`Last day, ${bill.reference_prefix}`} value={on}
            onChange={(e) => setOn(e.target.value)}
            className="h-8 rounded-sm border border-line-strong bg-sunken px-2 text-sm text-ink" />
          <Button type="submit" variant="ghost">Stop it</Button>
        </form>
      )}
    </li>
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
  return (
    <form
      className="flex flex-wrap items-end gap-3"
      onSubmit={async (event) => {
        event.preventDefault();
        const body = {
          vendor_id: vendor, account, reference_prefix: prefix, amount, memo,
          day_of_month: Number(dayOfMonth), starts_on: starts,
        };
        if (await record("/api/payables/recurring", body, () => `${prefix} is entered each month.`)) {
          setPrefix("");
          setAmount("");
          setMemo("");
        }
      }}
    >
      <Select label="Recurring vendor" id="recurring-vendor" value={vendor}
        onChange={(e) => setVendor(e.target.value)}>
        {books.vendors.map((v) => (
          <option key={v.id} value={v.id}>
            {v.name}
          </option>
        ))}
      </Select>
      <Select label="For owner and property" id="recurring-account" value={account}
        onChange={(e) => setAccount(e.target.value)}>
        {books.accounts.map((a) => (
          <option key={a.account} value={a.account}>
            {a.owner}, {a.property}
          </option>
        ))}
      </Select>
      <Field label="Reference prefix" id="recurring-prefix" required maxLength={100} value={prefix}
        onChange={(e) => setPrefix(e.target.value)} />
      <Field label="Monthly amount" id="recurring-amount" inputMode="decimal" required
        value={amount} onChange={(e) => setAmount(e.target.value)} />
      <Field label="For" id="recurring-memo" required maxLength={200} value={memo}
        onChange={(e) => setMemo(e.target.value)} />
      <Field label="Day of the month" id="recurring-day" type="number" min={1} max={28} required
        value={dayOfMonth} onChange={(e) => setDayOfMonth(e.target.value)} />
      <Field label="First day" id="recurring-starts" type="date" required value={starts}
        onChange={(e) => setStarts(e.target.value)} />
      <Button type="submit" variant="secondary">Add recurring bill</Button>
    </form>
  );
}
