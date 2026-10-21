import { useEffect, useState } from "react";
import type { Me } from "../api";
import { ApiError, api } from "../api";
import { Badge } from "../components/Badge";
import { Button } from "../components/Button";
import { Notice } from "../components/Notice";
import { Said } from "../components/Said";
import { Card, PageHeader, Shell } from "../components/Shell";
import { SidePanel } from "../components/SidePanel";
import { Wide } from "../components/Wide";
import { money, usDate } from "../money";
import type { BillPageData } from "../payables";
import { billBadge, emailNote, markupLabel } from "../payables";
import type { Portfolio } from "../setup";
import { ApprovalForm, DeclineForm, ExpenseAccountForm, PayForm, SetAsideForm } from "./BillForms";
import { FilesCard } from "./FilesCard";
import { GoLink } from "./NeedsAttention";
import { STAFF_NAV } from "./staffNav";
import type { Recorder } from "./useRecord";
import { useRecord } from "./useRecord";

const LINK = "font-medium text-iris hover:text-iris-hover hover:underline";

type Step = "approve" | "decline" | "set-aside" | "pay" | "expense";
const TITLES: Record<Step, string> = {
  approve: "Record approval",
  decline: "Record decline",
  "set-aside": "Set aside",
  pay: "Pay",
  expense: "Expense accounts",
};

// A bill's own page (ADR 0006): what it is and its status, its next step beside its title, its
// markup, the invoice shared across properties it is a share of, its expense account lines (split
// or one), its invoice and receipts, the email asking its owner to decide it and what happened to
// it, oldest first.
export function BillPage({ me, id }: { me: Me; id: string }) {
  const [found, setFound] = useState<BillPageData | null>(null);
  const [books, setBooks] = useState<Portfolio | null>(null);
  const [error, setError] = useState("");
  const [step, setStep] = useState<Step | null>(null);
  const [runs, setRuns] = useState(0);
  const { said, error: refused, record } = useRecord(() => setRuns((n) => n + 1));

  useEffect(() => {
    api.get<BillPageData>(`/api/payables/bills/${id}`).then(setFound)
      .catch((why) => setError(why instanceof ApiError ? why.detail : String(why)));
  }, [id, runs]);
  useEffect(() => {
    api.get<Portfolio>("/api/setup").then(setBooks).catch(() => setBooks(null));
  }, []);

  const recordThenClose: Recorder = async (path, body, done) => {
    const recorded = await record(path, body, done);
    if (recorded) setStep(null);
    return recorded;
  };

  if (found === null) {
    return (
      <Shell me={me} nav={STAFF_NAV}>
        <PageHeader title="Bill" />
        <Notice tone={error ? "error" : "info"}>{error || "Loading"}</Notice>
      </Shell>
    );
  }
  const { bill } = found;
  const badge = billBadge(bill);
  const note = emailNote(bill);
  const markup = markupLabel(bill);
  const steps: { step: Step; label: string; primary?: boolean }[] =
    bill.status === "needs approval" ? [{ step: "approve", label: "Record approval", primary: true }, { step: "decline", label: "Record decline" }]
      : bill.status === "to set aside" ? [{ step: "set-aside", label: "Set aside", primary: true }]
        : bill.status === "set aside" ? [{ step: "pay", label: "Pay", primary: true }]
          : [];
  return (
    <Shell me={me} nav={STAFF_NAV}>
      <PageHeader title={`${bill.vendor} ${bill.reference}`} actions={
        <div className="flex flex-wrap items-center gap-2">
          {steps.map((s) => (
            <Button key={s.step} variant={s.primary ? "primary" : "secondary"} onClick={() => setStep(s.step)}>{s.label}</Button>
          ))}
        </div>
      } />
      <p className="flex flex-wrap items-center gap-3 text-base text-ink-muted">
        <GoLink href="/bills" className={LINK}>Bills</GoLink>
        <Badge tone={badge.tone} data-testid="status">{badge.word}</Badge>
        <span className="font-medium text-ink" data-testid="bill-amount">{money(bill.amount)}</span>
      </p>
      <Said>{said}</Said>
      {step === null && <Notice tone="error">{refused}</Notice>}
      <Card title="Summary">
        <dl className="grid grid-cols-2 gap-x-6 gap-y-2 text-base md:grid-cols-3" aria-label="Summary">
          <Item label="Owner">{bill.owner}</Item>
          <Item label="Property"><GoLink href={`/properties/${bill.property_id}`} className={LINK}>{bill.property}</GoLink></Item>
          <Item label="Vendor"><GoLink href={`/vendors/${bill.vendor_id}`} className={LINK}>{bill.vendor}</GoLink></Item>
          <Item label="Bill date">{usDate(bill.bill_date)}</Item>
          <Item label="Due">{usDate(bill.due_on)}</Item>
          <Item label="Memo">{bill.memo}</Item>
          <Item label="Owner approves over">{money(bill.approval_limit)}</Item>
          {markup && <Item label="Markup">{markup}</Item>}
          {bill.work_order_id && (
            <Item label="Work order"><GoLink href={`/work-orders/${bill.work_order_id}`} className={LINK}>Open it</GoLink></Item>
          )}
        </dl>
        {note && <p className="mt-3 text-sm text-ink-subtle">{note}</p>}
        {bill.declined_by && (
          <p className="mt-3 text-sm text-ink-subtle">
            Declined by {bill.declined_by}: {bill.decline_reason}. It is never paid from the owner's money.
          </p>
        )}
      </Card>
      {found.shared && (
        <Card title={`Shared across properties: ${found.shared.reference}, ${money(found.shared.amount)}`}>
          <Wide>
            <table className="w-full text-base" aria-label="Shares">
              <thead>
                <tr>
                  <th className="text-left">Ref #</th>
                  <th className="text-left">Owner</th>
                  <th className="text-left">Property</th>
                  <th className="text-right">Amount</th>
                  <th className="text-left">Status</th>
                </tr>
              </thead>
              <tbody>
                {found.shared.parts.map((part) => (
                  <tr key={part.bill_id}>
                    <td>
                      {part.bill_id === bill.id ? part.reference
                        : <GoLink href={`/bills/${part.bill_id}`} className={LINK}>{part.reference}</GoLink>}
                    </td>
                    <td>{part.owner}</td>
                    <td>{part.property}</td>
                    <td className="text-right">{money(part.amount)}</td>
                    <td><PartBadge bill={bill} status={part.status} /></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </Wide>
        </Card>
      )}
      <Card title="Expense accounts">
        <Wide>
          <table className="w-full text-base" aria-label="Lines">
            <thead>
              <tr>
                <th className="text-left">Expense account</th>
                <th className="text-right">Amount</th>
              </tr>
            </thead>
            <tbody>
              {found.lines.map((line, n) => (
                <tr key={n}>
                  <td>{line.expense_account ?? <span className="text-ink-subtle">None yet</span>}</td>
                  <td className="text-right">{money(line.amount)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </Wide>
        {books && books.expense_accounts.length > 0 && bill.status !== "declined" && (
          <div className="mt-3"><Button variant="secondary" onClick={() => setStep("expense")}>Change</Button></div>
        )}
      </Card>
      <FilesCard kind="receipt" aboutId={bill.id} name={`${bill.vendor} ${bill.reference}`} />
      {found.email && (
        <Card title="Email to the owner">
          <p className="text-base text-ink" data-testid="bill-email">
            {found.email.subject}, to {found.email.to}, written {usDate(found.email.written_at)}:{" "}
            {found.email.status === "sent" ? "sent." : found.email.status === "waiting" ? "waiting to be sent." : "not sent"}
            {found.email.status !== "sent" && found.email.error ? ` (${found.email.error}, after ${found.email.attempts} tries)` : ""}
          </p>
        </Card>
      )}
      <Card title="History">
        <ol className="flex flex-col gap-1 text-base" aria-label="History">
          {found.history.map((event, n) => (
            <li key={n}><span className="text-ink-muted">{usDate(event.on)}</span> {event.what}</li>
          ))}
        </ol>
      </Card>
      {step !== null && (
        <SidePanel title={TITLES[step]} onClose={() => setStep(null)}>
          <p className="text-base text-ink">{bill.vendor} {bill.reference}, {money(bill.amount)}</p>
          {step === "approve" && <ApprovalForm bill={bill} record={recordThenClose} />}
          {step === "decline" && <DeclineForm bill={bill} record={recordThenClose} />}
          {step === "set-aside" && <SetAsideForm bill={bill} record={recordThenClose} />}
          {step === "pay" && <PayForm bill={bill} record={recordThenClose} />}
          {step === "expense" && books && (
            <ExpenseAccountForm bill={bill} books={books} record={recordThenClose}
              current={found.lines.filter((l) => l.expense_account_id).map((l) => ({ account: l.expense_account_id ?? "", amount: l.amount }))} />
          )}
          <Notice tone="error">{refused}</Notice>
        </SidePanel>
      )}
    </Shell>
  );
}

// A share's status as the list reads a bill's: approved, set aside, paid...
function PartBadge({ bill, status }: { bill: BillPageData["bill"]; status: BillPageData["bill"]["status"] }) {
  const badge = billBadge({ ...bill, status, overdue: false });
  return <Badge tone={badge.tone}>{badge.word}</Badge>;
}

function Item({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div>
      <dt className="text-sm text-ink-muted">{label}</dt>
      <dd>{children}</dd>
    </div>
  );
}
