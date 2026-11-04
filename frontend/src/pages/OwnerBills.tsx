import { Receipt } from "lucide-react";
import { useEffect, useState } from "react";
import { Dialog, Modal, ModalOverlay } from "react-aria-components";
import { ApiError, api } from "../api";
import type { BadgeTone } from "../components/Badge";
import { Badge } from "../components/Badge";
import { Button } from "../components/Button";
import { Field } from "../components/Field";
import { EmptyState } from "../components/EmptyState";
import { Notice } from "../components/Notice";
import { Card } from "../components/Shell";
import { Wide } from "../components/Wide";
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
  // Where it stands for the owner (src/pms/api/owner.py, owner_status).
  status: "waiting" | "approved" | "paid" | "short" | "declined" | "void";
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

// Where a bill stands for its owner, as the server says (src/pms/api/owner.py, owner_status).
const STATUS: Record<OwnerBill["status"], { label: string; tone: BadgeTone }> = {
  waiting: { label: "Waiting for you", tone: "caution" },
  approved: { label: "Approved", tone: "positive" },
  paid: { label: "Paid", tone: "neutral" },
  short: { label: "Short on funds", tone: "danger" },
  declined: { label: "Declined", tone: "neutral" },
  void: { label: "Void", tone: "neutral" },
};

// Declining asks why, which staff see with the bill; a declined bill is never paid from the
// owner's account.
function DeclineDialog({ bill, onDeclined, onCancel }: {
  bill: OwnerBill;
  onDeclined: () => void;
  onCancel: () => void;
}) {
  const [reason, setReason] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  async function decline() {
    setError("");
    setBusy(true);
    try {
      await api.post(`/api/owner/bills/${bill.id}/decline`, { reason });
      onDeclined();
    } catch (failed) {
      setError(failed instanceof ApiError ? failed.detail : String(failed));
    } finally {
      setBusy(false);
    }
  }
  return (
    <ModalOverlay isOpen isDismissable onOpenChange={(open) => open || onCancel()}
      className="fixed inset-0 z-40 bg-scrim">
      <Modal className="fixed top-1/4 left-1/2 z-50 w-[440px] max-w-[calc(100vw-2rem)] -translate-x-1/2 rounded-md border border-card-edge bg-canvas p-6 shadow-lg">
        <Dialog aria-label="Decline this bill?" className="flex flex-col gap-4 outline-none">
          <h2 className="text-lg font-semibold text-ink">Decline this bill?</h2>
          <p className="text-base">
            {bill.vendor}, {bill.reference}, {money(billTotal(bill))} for {bill.property}. It is never paid from your
            account.
          </p>
          <Field label="Why you decline it" id={`decline-${bill.id}`} value={reason} autoFocus
            onChange={(e) => setReason(e.target.value)} />
          <Notice tone="error">{error}</Notice>
          <div className="flex justify-end gap-2">
            <Button type="button" variant="secondary" onClick={onCancel}>Cancel</Button>
            <Button type="button" variant="danger" disabled={busy || !reason.trim()} onClick={() => void decline()}>
              Decline
            </Button>
          </div>
        </Dialog>
      </Modal>
    </ModalOverlay>
  );
}

const CELL = "text-left";
const AMOUNT = "text-right";

// The signed-in owner's bills that are theirs to decide or to fund (over their approval limit,
// decided, or short of their money), in a table: vendor, bill #, property, what it takes from
// their account (with any markup), the day it is due, where it stands and what they can do.
// Approving lets staff pay it from the owner's money; declining means it is never paid from it.
export function OwnerBills() {
  const [bills, setBills] = useState<OwnerBill[] | null>(null);
  const [error, setError] = useState("");
  const [done, setDone] = useState("");
  const [declining, setDeclining] = useState<OwnerBill | null>(null);
  const [approving, setApproving] = useState<OwnerBill | null>(null);

  function load() {
    api.get<OwnerBill[]>("/api/owner/bills").then(setBills)
      .catch((failed) => setError(failed instanceof ApiError ? failed.detail : String(failed)));
  }
  useEffect(load, []);

  const waiting = bills?.filter((b) => b.status === "waiting").length ?? 0;
  return (
    <Card title="Bills waiting for you">
      {approving && (
        <ApproveDialog bill={approving} onCancel={() => setApproving(null)} onApproved={() => {
          setDone(`Approved ${approving.vendor}'s ${approving.reference}.`);
          setApproving(null);
          load();
        }} />
      )}
      {declining && (
        <DeclineDialog bill={declining} onCancel={() => setDeclining(null)} onDeclined={() => {
          setDone(`Declined ${declining.vendor}'s ${declining.reference}.`);
          setDeclining(null);
          load();
        }} />
      )}
      <Notice tone="error">{error}</Notice>
      <Said>{done}</Said>
      {bills === null ? (
        error ? null : <Notice tone="info">Loading</Notice>
      ) : bills.length === 0 ? (
        <EmptyState icon={Receipt} title="No bills need you." body="Bills over your approval limit show here." />
      ) : (
        <>
          <p className="text-sm text-ink-muted" data-testid="waiting-count">
            {waiting === 0 ? "No bill waits for your approval." : waiting === 1 ? "1 bill waits for your approval." : `${waiting} bills wait for your approval.`}
          </p>
          <Wide>
            <table className="w-full text-base" aria-label="Your bills">
              <thead className="text-sm text-ink-subtle">
                <tr>
                  <th className={CELL}>Vendor</th>
                  <th className={CELL}>Bill #</th>
                  <th className={CELL}>Property</th>
                  <th className={AMOUNT}>Amount</th>
                  <th className={CELL}>Due</th>
                  <th className={CELL}>Status</th>
                  <th className={CELL}><span className="sr-only">Action</span></th>
                </tr>
              </thead>
              <tbody>
                {bills.map((bill) => (
                  <tr key={bill.id} className="border-t border-line align-top" aria-label={`${bill.vendor} ${bill.reference}`}>
                    <td className={CELL}>
                      {bill.vendor}
                      <span className="block text-sm text-ink-subtle">{bill.memo}</span>
                    </td>
                    <td className={CELL}>{bill.reference}</td>
                    <td className={CELL}>{bill.property}</td>
                    <td className={AMOUNT}>
                      {money(billTotal(bill))}
                      {bill.markup !== null && (
                        <span className="block text-sm text-ink-subtle" data-testid="markup">
                          incl. {money(bill.markup)} markup
                        </span>
                      )}
                    </td>
                    <td className={CELL}>{usDate(bill.due_on)}</td>
                    <td className={CELL}>
                      <Badge tone={STATUS[bill.status].tone}>{STATUS[bill.status].label}</Badge>
                      {bill.status === "declined" && bill.reason && (
                        <span className="block text-sm text-ink-subtle">{bill.reason}</span>
                      )}
                      {(bill.status === "waiting" || bill.status === "short") && bill.needed !== null && (
                        <span className="block text-sm" data-testid="needed">Send {money(bill.needed)}</span>
                      )}
                    </td>
                    <td className={CELL}>
                      {bill.status === "waiting" ? (
                        <div className="flex flex-wrap gap-2">
                          <Button size="sm" onClick={() => { setError(""); setDone(""); setApproving(bill); }}>
                            Approve…
                          </Button>
                          <Button size="sm" variant="secondary" onClick={() => { setError(""); setDone(""); setDeclining(bill); }}>
                            Decline…
                          </Button>
                        </div>
                      ) : null}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </Wide>
        </>
      )}
    </Card>
  );
}
