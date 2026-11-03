import { useEffect, useState } from "react";
import { Dialog, Modal, ModalOverlay } from "react-aria-components";
import { ApiError, api } from "../api";
import { Badge } from "../components/Badge";
import { Button } from "../components/Button";
import { Field } from "../components/Field";
import { Notice } from "../components/Notice";
import { Card } from "../components/Shell";
import { money, usDate } from "../money";
import { Said } from "../components/Said";

export type OwnerBill = {
  id: string;
  vendor: string;
  reference: string;
  property: string;
  bill_date: string;
  due_on: string;
  amount: string;
  memo: string;
  approval_limit: string;
  // The company's markup on it, at the percent the owner's agreement sets; null: none.
  markup: string | null;
  markup_percent: string | null;
  // Void: staff voided it (entered by mistake).
  decision: "approved" | "declined" | "void" | null;
  decided_on: string | null;
  decided_by: string | null;
  reason: string | null;
  // While the owner's balance is short of it: by how much, and what they need to send for it and
  // the bills due before it (ADR 0010).
  short: string | null;
  needed: string | null;
  // What the property's account holds now; approving it leaves this less the bill and markup.
  balance: string;
  // Whether approving it takes the owner's typed full name (over the company's amount).
  name_needed: boolean;
};

// The bill with its markup: what approving it lets us take from the owner's account.
export function billTotal(bill: Pick<OwnerBill, "amount" | "markup">): string {
  const cents = Math.round(Number(bill.amount) * 100) + Math.round(Number(bill.markup ?? "0") * 100);
  return (cents / 100).toFixed(2);
}

// What the account holds once the bill is paid: the balance less the bill and its markup.
export function balanceAfter(bill: Pick<OwnerBill, "amount" | "markup" | "balance">): string {
  const cents = Math.round(Number(bill.balance) * 100) - Math.round(Number(billTotal(bill)) * 100);
  return (cents / 100).toFixed(2);
}

// Approving a bill asks first: what it is, what it takes and what the account holds before and
// after. Over the company's amount the owner types their full name, which the server checks.
function ApproveDialog({ bill, onApproved, onCancel }: {
  bill: OwnerBill;
  onApproved: () => void;
  onCancel: () => void;
}) {
  const [name, setName] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  async function approve() {
    setError("");
    setBusy(true);
    try {
      await api.post(`/api/owner/bills/${bill.id}/approval`, bill.name_needed ? { typed_name: name } : {});
      onApproved();
    } catch (failed) {
      setError(failed instanceof ApiError ? failed.detail : String(failed));
    } finally {
      setBusy(false);
    }
  }
  const rows: [string, string][] = [
    ["Vendor", bill.vendor],
    ["Bill #", bill.reference],
    ["Property", bill.property],
    ["Amount", money(bill.amount)],
    ...(bill.markup !== null ? [["Markup", money(bill.markup)] as [string, string]] : []),
    ["Balance now", money(bill.balance)],
    ["Balance after", money(balanceAfter(bill))],
  ];
  return (
    <ModalOverlay isOpen isDismissable onOpenChange={(open) => open || onCancel()}
      className="fixed inset-0 z-40 bg-scrim">
      <Modal className="fixed top-1/4 left-1/2 z-50 w-[440px] max-w-[calc(100vw-2rem)] -translate-x-1/2 rounded-md border border-card-edge bg-canvas p-6 shadow-lg">
        <Dialog aria-label="Approve this bill?" className="flex flex-col gap-4 outline-none">
          <h2 className="text-lg font-semibold text-ink">Approve this bill?</h2>
          <dl className="grid grid-cols-2 gap-x-6 gap-y-2 text-base" aria-label="The bill">
            {rows.map(([label, value]) => (
              <div key={label} className="contents">
                <dt className="text-sm text-ink-muted">{label}</dt>
                <dd className="text-right">{value}</dd>
              </div>
            ))}
          </dl>
          {bill.name_needed && (
            <Field label="Type your full name to approve" id="typed-name" autoComplete="name"
              value={name} onChange={(e) => setName(e.target.value)} />
          )}
          <Notice tone="error">{error}</Notice>
          <div className="flex justify-end gap-2">
            <Button type="button" variant="secondary" onClick={onCancel}>Cancel</Button>
            <Button type="button" autoFocus={!bill.name_needed} disabled={busy || (bill.name_needed && !name.trim())}
              onClick={() => void approve()}>
              Approve
            </Button>
          </div>
        </Dialog>
      </Modal>
    </ModalOverlay>
  );
}

// The signed-in owner's bills over their approval limit: each waiting one to approve, or to
// decline with a reason, and those decided; and the bills their balance is short of, with what to
// send. Approving lets staff pay it from the owner's money; declining means it is never paid from
// it. Nothing moves here.
export function OwnerBills() {
  const [bills, setBills] = useState<OwnerBill[] | null>(null);
  const [error, setError] = useState("");
  const [done, setDone] = useState("");
  const [declining, setDeclining] = useState<string | null>(null);
  const [approving, setApproving] = useState<OwnerBill | null>(null);
  const [reason, setReason] = useState("");

  function show(failed: unknown) {
    setError(failed instanceof ApiError ? failed.detail : String(failed));
  }

  function load() {
    api.get<OwnerBill[]>("/api/owner/bills").then(setBills).catch(show);
  }
  useEffect(load, []);

  function decline(bill: OwnerBill) {
    setError("");
    setDone("");
    api
      .post(`/api/owner/bills/${bill.id}/decline`, { reason })
      .then(() => {
        setDone(`Declined ${bill.vendor}'s ${bill.reference}.`);
        setDeclining(null);
        setReason("");
        load();
      })
      .catch(show);
  }

  const waiting = bills?.filter((b) => b.decision === null && Number(b.amount) > Number(b.approval_limit)) ?? [];
  // Short of money, and not waiting for a decision: what to send.
  const short = bills?.filter((b) => b.needed !== null && b.decision !== "void" && b.decision !== "declined"
    && !(b.decision === null && Number(b.amount) > Number(b.approval_limit))) ?? [];
  const decided = bills?.filter((b) => b.decision !== null) ?? [];
  return (
    <Card title="Bills waiting for you">
      {approving && (
        <ApproveDialog bill={approving} onCancel={() => setApproving(null)} onApproved={() => {
          setDone(`Approved ${approving.vendor}'s ${approving.reference}.`);
          setApproving(null);
          load();
        }} />
      )}
      {(error || done) && (
        <div className="flex flex-col gap-1">
          <Notice tone="error">{error}</Notice>
          <Said>{done}</Said>
        </div>
      )}
      {bills === null ? (
        error ? null : <Notice tone="info">Loading</Notice>
      ) : waiting.length === 0 ? (
        <p className="text-sm text-ink-muted">No bill waits for your approval.</p>
      ) : (
        <ul className="flex flex-col gap-4" aria-label="Waiting for you">
          {waiting.map((bill) => (
            <li key={bill.id} className="border-t border-line pt-3">
              <p className="text-base">
                {bill.vendor}, {bill.reference}: <span className="font-medium">{money(bill.amount)}</span>
              </p>
              <p className="text-sm text-ink-muted">
                {bill.property}: {bill.memo}. Billed {usDate(bill.bill_date)}, due {usDate(bill.due_on)}. Over your
                approval limit of {money(bill.approval_limit)}.
              </p>
              {bill.markup !== null && (
                <p className="text-sm text-ink-muted" data-testid="markup">
                  Our markup of {bill.markup_percent}%, {money(bill.markup)}, as your management agreement
                  sets, is taken with it.
                </p>
              )}
              {bill.needed !== null && (
                <p className="text-sm text-ink-muted" data-testid="needed">
                  Your balance is {money(bill.short)} short of it: please send {money(bill.needed)}.
                </p>
              )}
              <div className="mt-2 flex flex-wrap items-end gap-3">
                <Button onClick={() => { setError(""); setDone(""); setApproving(bill); }}>Approve…</Button>
                {declining === bill.id ? (
                  <>
                    <Field label="Why you decline it" id={`decline-${bill.id}`} value={reason}
                      onChange={(e) => setReason(e.target.value)} />
                    <Button variant="secondary" disabled={!reason.trim()} onClick={() => decline(bill)}>
                      Decline
                    </Button>
                  </>
                ) : (
                  <Button variant="secondary" onClick={() => { setDeclining(bill.id); setReason(""); }}>
                    Decline…
                  </Button>
                )}
              </div>
              <p className="mt-1 text-sm text-ink-subtle">
                Approve: we may pay it from your account. Decline: it is never paid from your account.
              </p>
            </li>
          ))}
        </ul>
      )}
      {short.length > 0 && (
        <ul className="mt-4 flex flex-col gap-2 text-sm" aria-label="Your balance is short">
          {short.map((bill) => (
            <li key={bill.id} data-testid="short-bill">
              {bill.vendor}, {bill.reference} ({money(bill.amount)}, {bill.property}, due {usDate(bill.due_on)}): your
              balance is {money(bill.short)} short of it. Please send {money(bill.needed)} so we can pay it. Nothing is
              paid or charged until it arrives.
            </li>
          ))}
        </ul>
      )}
      {decided.length > 0 && (
        <ul className="mt-4 flex flex-col gap-2 text-sm" aria-label="Decided">
          {decided.map((bill) => (
            <li key={bill.id} className="flex flex-wrap items-center gap-2">
              <Badge tone={bill.decision === "approved" ? "positive" : "neutral"}>{bill.decision === "void" ? "void" : bill.decision}</Badge>
              <span>
                {bill.vendor}, {bill.reference} ({money(bill.amount)}) on {usDate(bill.decided_on)} by {bill.decided_by}
                {bill.reason ? `. ${bill.reason}` : ""}
              </span>
            </li>
          ))}
        </ul>
      )}
    </Card>
  );
}
