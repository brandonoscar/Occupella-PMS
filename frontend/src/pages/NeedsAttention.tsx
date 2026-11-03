import { CircleCheck } from "lucide-react";
import { useState } from "react";
import type { Me } from "../api";
import { Button } from "../components/Button";
import { useConfirm } from "../components/Confirm";
import { EmptyState } from "../components/EmptyState";
import { Notice } from "../components/Notice";
import { Said } from "../components/Said";
import { SidePanel } from "../components/SidePanel";
import { usDate } from "../money";
import { approver } from "../reconcile";
import { navigate } from "../router";
import type { Action, AttentionRow, Today } from "../today";
import { attentionRows } from "../today";
import { ApproveDrawsForm, ReminderForm, approveQuestion } from "./AttentionActions";
import { PayForm } from "./BillForms";
import type { Recorder } from "./useRecord";
import { useRecord } from "./useRecord";

type Panel = { title: string; action: Action };

// What is late or waits on someone today, as one list, the longest overdue first and then what
// comes due soonest (the Dashboard's "Needs attention"; ADR 0010 phase 3). Each row carries its
// main action: Send reminder, Pay bill, Approve draws and Approve reconciliation are done right
// here, each showing who gets it or asking before money moves; Review application and the rest
// open the page that does that work.
export function NeedsAttention({ today, me, refresh }: { today: Today; me: Me; refresh: () => void }) {
  const rows = attentionRows(today);
  const [panel, setPanel] = useState<Panel | null>(null);
  const { said, error, record } = useRecord(refresh);
  const { ask, prompt } = useConfirm();
  const recordThenClose: Recorder = async (path, body, done) => {
    const recorded = await record(path, body, done);
    if (recorded) setPanel(null);
    return recorded;
  };

  if (rows.length === 0) {
    return (
      <>
        <Said>{said}</Said>
        <EmptyState icon={CircleCheck} title="Nothing is late or waiting."
          body="Late rent, bills due this week, owner draws ready to pay, month-end, applications, unsigned offers, journal entries waiting, vendor insurance running out, the company's fees left in trust, trust accounts' signers, renters insurance, security deposits still to receive, deposits due back and leases gone month to month show here." />
      </>
    );
  }

  function button(row: AttentionRow) {
    const { action } = row;
    const open = (title: string) => (
      <Button size="sm" variant="secondary" onClick={() => setPanel({ title, action })}>{title}</Button>
    );
    switch (action.kind) {
      case "remind":
        return open("Send reminder");
      case "pay":
        return open("Pay bill");
      case "draws":
        return open("Approve draws");
      case "approve": {
        const who = approver(me, action.prepared);
        if (who === "may") {
          return (
            <Button size="sm" variant="secondary" onClick={() => ask(approveQuestion(action.account, action.prepared),
              () => record(`/api/reconcile/${action.account.bank_account_id}/approve`, { preparation_id: action.prepared.id },
                () => `Approved ${action.account.name}'s month-end through ${usDate(action.prepared.through)}.`), "Approve")}>
              Approve reconciliation
            </Button>
          );
        }
        return (
          <span className="text-sm text-ink-muted">
            {who === "preparer" ? "You prepared it: someone else approves" : "Needs someone who holds approve"}
          </span>
        );
      }
      case "go":
        return <GoLink href={action.href}>{action.label}</GoLink>;
    }
  }

  return (
    <>
      <Said>{said}</Said>
      <Notice tone="error">{error}</Notice>
      <ol aria-label="Needs attention" className="divide-y divide-line rounded-md border border-card-edge">
        {rows.map((row) => (
          <li key={row.key} aria-label={row.what} className="flex flex-wrap items-center justify-between gap-3 px-4 py-3">
            <div className="flex min-w-0 flex-1 flex-col gap-0.5">
              <span className="text-xs font-medium tracking-wider text-ink-subtle uppercase">{row.what}</span>
              <span className="text-base">{row.line}</span>
            </div>
            <div className="shrink-0">{button(row)}</div>
          </li>
        ))}
      </ol>
      {panel && (
        <SidePanel title={panel.title} onClose={() => setPanel(null)}>
          {panel.action.kind === "remind" && <ReminderForm leaseId={panel.action.leaseId} record={recordThenClose} />}
          {panel.action.kind === "pay" && <PayForm bill={panel.action.bill} record={recordThenClose} />}
          {panel.action.kind === "draws" && <ApproveDrawsForm run={panel.action.run} record={recordThenClose} />}
          <Notice tone="error">{error}</Notice>
        </SidePanel>
      )}
      {prompt}
    </>
  );
}

// A link to another page of the portal, followed in place; a click for a new tab or window is
// the browser's.
export function GoLink({
  href,
  className = "text-sm text-iris hover:text-iris-hover hover:underline",
  children,
}: {
  href: string;
  className?: string;
  children: React.ReactNode;
}) {
  return (
    <a
      href={href}
      className={className}
      onClick={(event) => {
        if (event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey) return;
        event.preventDefault();
        navigate(href);
      }}
    >
      {children}
    </a>
  );
}
