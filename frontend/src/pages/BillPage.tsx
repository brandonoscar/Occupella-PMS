import { useEffect, useState } from "react";
import type { Me } from "../api";
import { ApiError, api } from "../api";
import { Badge } from "../components/Badge";
import { Button } from "../components/Button";
import { useConfirm } from "../components/Confirm";
import { Notice } from "../components/Notice";
import { RowMenu } from "../components/RowMenu";
import { Said } from "../components/Said";
import { Card, PageHeader, Shell } from "../components/Shell";
import { SidePanel } from "../components/SidePanel";
import { Wide } from "../components/Wide";
import { latest } from "../latest";
import { money, usDate } from "../money";
import type { BillEmail, BillPageData } from "../payables";
import { billBadge, emailNote, holdLine, markupLabel, paymentHow } from "../payables";
import type { Portfolio } from "../setup";
import { ApprovalForm, DeclineForm, ExpenseAccountForm, PayForm, VoidForm } from "./BillForms";
import { FilesCard } from "./FilesCard";
import { GoLink } from "./NeedsAttention";
import { STAFF_NAV } from "./staffNav";
import type { Recorder } from "./useRecord";
import { useRecord } from "./useRecord";

const LINK = "font-medium text-iris hover:text-iris-hover hover:underline";

type Step = "approve" | "decline" | "pay" | "void" | "expense";
const TITLES: Record<Step, string> = {
  approve: "Record approval",
  decline: "Record decline",
  pay: "Record payment",
  void: "Void",
  expense: "Expense accounts",
};

// A bill's own page (ADR 0006, ADR 0010): what it is, its status and what it holds of the owner's
// balance in a sentence, its next step beside its title (Void in the ⋯ menu while nothing was
// taken for it), its markup, the invoice shared across properties it is a share of, its expense
// account lines (split or one), its payments, its invoice and receipts, the emails to its owner
// and what happened to it, oldest first.
export function BillPage({ me, id }: { me: Me; id: string }) {
  const [found, setFound] = useState<BillPageData | null>(null);
  const [books, setBooks] = useState<Portfolio | null>(null);
  const [error, setError] = useState("");
  const [step, setStep] = useState<Step | null>(null);
  const [runs, setRuns] = useState(0);
  const { said, error: refused, record } = useRecord(() => setRuns((n) => n + 1));
  const { ask, prompt } = useConfirm();

  useEffect(() => {
    const run = latest();
    api.get<BillPageData>(`/api/payables/bills/${id}`).then(run.keep(setFound))
      .catch(run.keep((why) => setError(why instanceof ApiError ? why.detail : String(why))));
    return run.stop;
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
    bill.status === "waiting for owner" ? [{ step: "approve", label: "Record approval", primary: true }, { step: "decline", label: "Record decline" }]
      : bill.status === "ready to pay" || bill.status === "partly paid" ? [{ step: "pay", label: "Record payment", primary: true }]
        : [];
  // Void while nothing was taken for it (rule 4: hidden when it doesn't apply).
  const voidable = bill.held_since === null && bill.status !== "paid" && bill.status !== "void" && bill.status !== "partly paid";
  const askOwner = found.ask;
  return (
    <Shell me={me} nav={STAFF_NAV}>
      <PageHeader title={`${bill.vendor} ${bill.reference}`} actions={
        <div className="flex flex-wrap items-center gap-2">
          {steps.map((s) => (
            <Button key={s.step} variant={s.primary ? "primary" : "secondary"} onClick={() => setStep(s.step)}>{s.label}</Button>
          ))}
          {askOwner && askOwner.to !== null && !askOwner.asked_today && (
            <Button variant={bill.status === "short" ? "primary" : "secondary"} onClick={() => ask(
              `Email ${askOwner.owner} at ${askOwner.to} asking them to send ${money(askOwner.amount)}? Nothing is paid or charged; the bill is ready to pay once the money arrives.`,
              () => record(`/api/payables/bills/${bill.id}/ask-owner`, {}, () => `${askOwner.owner} is asked to send ${money(askOwner.amount)}.`),
              "Send email")}>
              Ask {askOwner.owner} for {money(askOwner.amount)}
            </Button>
          )}
          {voidable && (
            <RowMenu label={`${bill.vendor} ${bill.reference}`} actions={[
              { label: "Void", danger: true, onSelect: () => setStep("void") },
            ]} />
          )}
        </div>
      } />
      <p className="flex flex-wrap items-center gap-3 text-base text-ink-muted">
        <GoLink href="/bills" className={LINK}>Bills</GoLink>
        <Badge tone={badge.tone} data-testid="status">{badge.word}</Badge>
        {bill.overdue && <Badge tone="danger">Overdue</Badge>}
        <span className="font-medium text-ink" data-testid="bill-amount">{money(bill.amount)}</span>
      </p>
      <p className="mt-1 text-base text-ink" data-testid="hold">{holdLine(bill)}</p>
      {askOwner && askOwner.to === null && (
        <p className="text-sm text-ink-subtle">
          {askOwner.owner} has no email on file to ask.{" "}
          <GoLink href={`/owners/${bill.owner_id}`} className={LINK}>Add one to their contact details</GoLink>.
        </p>
      )}
      {askOwner?.asked_today && (
        <p className="text-sm text-ink-subtle">{askOwner.owner} was asked for it today; you can ask again tomorrow.</p>
      )}
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
          <Item label="Left to pay">{money(bill.left)}</Item>
          {markup && <Item label="Markup">{markup}</Item>}
          {bill.work_order_id && (
            <Item label="Work order"><GoLink href={`/work-orders/${bill.work_order_id}`} className={LINK}>Open it</GoLink></Item>
          )}
        </dl>
        {note && <p className="mt-3 text-sm text-ink-subtle">{note}</p>}
        {bill.declined_by && (
          <p className="mt-3 text-sm text-ink-subtle">
            {bill.voided ? "Voided" : "Declined"} by {bill.declined_by}: {bill.decline_reason}. It is never paid from the owner's money.
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
        {books && books.expense_accounts.length > 0 && bill.status !== "void" && (
          <div className="mt-3"><Button variant="secondary" onClick={() => setStep("expense")}>Change</Button></div>
        )}
      </Card>
      {found.payments.length > 0 && (
        <Card title="Payments">
          <Wide>
            <table className="w-full text-base" aria-label="Payments">
              <thead>
                <tr>
                  <th className="text-left">Paid on</th>
                  <th className="text-right">Amount</th>
                  <th className="text-left">How</th>
                  <th className="text-left">Bank statement</th>
                </tr>
              </thead>
              <tbody>
                {found.payments.map((payment) => (
                  <tr key={payment.transfer_id}>
                    <td>{usDate(payment.on)}</td>
                    <td className="text-right">{money(payment.amount)}</td>
                    <td>{paymentHow(payment)}</td>
                    <td>{payment.cleared_on ? `Cleared ${usDate(payment.cleared_on)}` : <span className="text-ink-subtle">Not on a statement yet</span>}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </Wide>
        </Card>
      )}
      <FilesCard kind="receipt" aboutId={bill.id} name={`${bill.vendor} ${bill.reference}`} />
      {(found.email || found.asks.length > 0) && (
        <Card title="Emails to the owner">
          <ul className="flex flex-col gap-1 text-base">
            {found.email && <EmailLine email={found.email} testId="bill-email" />}
            {found.asks.map((asked, n) => <EmailLine key={n} email={asked} testId="ask-email" />)}
          </ul>
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
          {step === "pay" && <PayForm bill={bill} record={recordThenClose} />}
          {step === "void" && <VoidForm bill={bill} record={recordThenClose} />}
          {step === "expense" && books && (
            <ExpenseAccountForm bill={bill} books={books} record={recordThenClose}
              current={found.lines.filter((l) => l.expense_account_id).map((l) => ({ account: l.expense_account_id ?? "", amount: l.amount }))} />
          )}
          <Notice tone="error">{refused}</Notice>
        </SidePanel>
      )}
      {prompt}
    </Shell>
  );
}

// An email to the bill's owner: what, to whom, when it was written and whether (and when) it went.
function EmailLine({ email, testId }: { email: BillEmail; testId: string }) {
  const went = email.status === "sent"
    ? `sent ${usDate(email.sent_at ?? email.written_at)}.`
    : email.status === "waiting" ? "waiting to be sent." : "not sent";
  return (
    <li data-testid={testId}>
      {email.subject}, to {email.to}, written {usDate(email.written_at)}: {went}
      {email.status !== "sent" && email.error ? ` (${email.error}, after ${email.attempts} tries)` : ""}
    </li>
  );
}

// A share's status as the list reads a bill's: ready to pay, short, paid...
function PartBadge({ bill, status }: { bill: BillPageData["bill"]; status: BillPageData["bill"]["status"] }) {
  const badge = billBadge({ ...bill, status });
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
