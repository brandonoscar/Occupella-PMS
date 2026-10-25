import { useEffect, useState } from "react";
import { ApiError, api } from "../api";
import { Badge } from "../components/Badge";
import { LinkButton } from "../components/Button";
import { Field } from "../components/Field";
import { Notice } from "../components/Notice";
import { Card } from "../components/Shell";
import { SidePanel } from "../components/SidePanel";
import { UpdatedStamp } from "../components/Updated";
import { Wide } from "../components/Wide";
import { latest } from "../latest";
import { day, money, usDate } from "../money";
import { downloadHref, FORMATS } from "../reports";
import type { LeaseDetail, Ledger, LedgerLine } from "../rentals";
import { LEDGER_KIND } from "../rentals";
import type { Recorder } from "./LeaseForms";
import { OpenChargeRow, PaymentRow } from "./LeaseForms";

const LINK = "font-medium text-iris hover:text-iris-hover hover:underline";
const WHOLE_DAY = /^\d{4}-\d{2}-\d{2}$/;

// The days asked of a ledger, as a query: From and To when each is a whole day.
function ledgerQuery(start: string, end: string, csv = false): string {
  const asked = new URLSearchParams();
  if (WHOLE_DAY.test(start)) asked.set("start", start);
  if (WHOLE_DAY.test(end)) asked.set("end", end);
  if (csv) asked.set("format", "csv");
  return asked.toString();
}

// Ledger (ADR 0010 phase 3, part M): each charge, payment and bounce with the balance after it,
// over the days asked (From empty: its first line; To empty: today) with the balance brought
// forward, and each line opens the charge or payment behind it in a side panel. Export CSV
// downloads the lines shown; the report below is the whole ledger. It is read again whenever the
// page is (something recorded, the browser tab back in view), and says when it was. Then each
// payment as it came in, whole, where It bounced is.
export function LedgerTab({ lease, runs, record }: { lease: LeaseDetail; runs: number; record: Recorder }) {
  const [start, setStart] = useState("");
  const [end, setEnd] = useState("");
  const [ledger, setLedger] = useState<Ledger | null>(null);
  const [error, setError] = useState("");
  const [at, setAt] = useState<Date | null>(null);
  const [open, setOpen] = useState<LedgerLine | null>(null);
  const query = ledgerQuery(start, end);
  useEffect(() => {
    const run = latest();
    setError("");
    api.get<Ledger>(`/api/leases/${lease.id}/ledger${query ? `?${query}` : ""}`)
      .then(run.keep((found: Ledger) => {
        setLedger(found);
        setAt(new Date());
      }))
      .catch(run.keep((why: unknown) => setError(why instanceof ApiError ? why.detail : String(why))));
    return run.stop;
  }, [lease.id, query, runs]);

  // What a panel records closes it once it is recorded.
  const recordThenClose: Recorder = async (path, body, done) => {
    const recorded = await record(path, body, done);
    if (recorded) setOpen(null);
    return recorded;
  };

  return (
    <div className="flex flex-col gap-6">
      <div className="flex flex-wrap items-end gap-3">
        <Field label="From" id="ledger-start" type="date" value={start} onChange={(e) => setStart(e.target.value)} />
        <Field label="To" id="ledger-end" type="date" value={end} onChange={(e) => setEnd(e.target.value)} />
        <LinkButton href={`/api/leases/${lease.id}/ledger?${ledgerQuery(start, end, true)}`}>Export CSV</LinkButton>
        <UpdatedStamp at={at} />
      </div>
      <Notice tone="error">{error}</Notice>
      {ledger === null ? (
        !error && <Notice tone="info">Loading</Notice>
      ) : ledger.lines.length === 0 && ledger.start === null ? (
        <p className="text-sm text-ink-subtle">Nothing charged or paid yet.</p>
      ) : (
        <Wide>
          <table className="w-full text-base" aria-label="Ledger">
            <thead>
              <tr>
                <th className="text-left">Date</th>
                <th className="text-left">Type</th>
                <th className="text-left">Memo</th>
                <th className="text-right">Charges</th>
                <th className="text-right">Payments</th>
                <th className="text-right">Balance</th>
              </tr>
            </thead>
            <tbody>
              {ledger.start !== null && (
                <tr aria-label="Balance forward">
                  <td className="whitespace-nowrap">{usDate(ledger.start)}</td>
                  <td colSpan={4}>Balance forward</td>
                  <td className="text-right">{money(ledger.opening)}</td>
                </tr>
              )}
              {ledger.lines.map((line, n) => (
                <tr key={n} className="cursor-pointer hover:bg-raised" onClick={() => setOpen(line)}>
                  <td className="whitespace-nowrap">{usDate(line.on)}</td>
                  <td>
                    <button type="button" className={LINK}
                      onClick={(event) => { event.stopPropagation(); setOpen(line); }}>
                      {LEDGER_KIND[line.kind]}
                    </button>
                    {line.bounced && <> <Badge tone="danger">Bounced</Badge></>}
                  </td>
                  <td className="text-ink-muted">{line.memo ?? ""}</td>
                  <td className="text-right">{line.charged ? money(line.charged) : ""}</td>
                  <td className="text-right">{line.paid ? money(line.paid) : ""}</td>
                  <td className="text-right">{money(line.balance)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </Wide>
      )}
      <div className="flex flex-wrap items-center gap-2" role="group" aria-label="Download the ledger">
        <span className="text-sm text-ink-muted">The whole ledger, as a report:</span>
        {FORMATS.map(([format, label]) => (
          <LinkButton key={format} href={downloadHref("tenant_ledger", [lease.id, day(new Date())], format)}>{label}</LinkButton>
        ))}
      </div>
      <Card title="Payments received">
        {lease.payments.length === 0 ? (
          <p className="text-sm text-ink-subtle">No payment yet.</p>
        ) : (
          <table className="w-full text-base" aria-label="Payments received">
            <thead>
              <tr>
                <th className="text-left">Received</th>
                <th className="text-right">Amount</th>
                <th><span className="sr-only">Actions</span></th>
              </tr>
            </thead>
            <tbody>
              {lease.payments.map((p) => <PaymentRow key={p.transfer_id} payment={p} record={record} />)}
            </tbody>
          </table>
        )}
      </Card>
      {open !== null && (
        <SidePanel title={LEDGER_TITLE[open.charge_id !== null ? "charge" : open.kind === "bounced payment" ? "bounce" : "payment"]}
          onClose={() => setOpen(null)}>
          <LedgerItem line={open} lease={lease} record={recordThenClose} />
        </SidePanel>
      )}
    </div>
  );
}

const LEDGER_TITLE = { charge: "Charge", payment: "Payment", bounce: "Bounced payment" } as const;

// What a ledger line opens: its charge, with what is still open of it and Waive; or its payment
// (a bounce's: the payment that bounced), with its receipt and It bounced.
function LedgerItem({ line, lease, record }: { line: LedgerLine; lease: LeaseDetail; record: Recorder }) {
  if (line.charge_id !== null) {
    const owing = lease.open_charges.find((c) => c.id === line.charge_id);
    return (
      <div className="flex flex-col gap-3">
        <dl className="grid grid-cols-[max-content_1fr] gap-x-6 gap-y-1 text-base" aria-label="The charge">
          <dt className="text-ink-muted">Due</dt>
          <dd>{usDate(line.on)}</dd>
          <dt className="text-ink-muted">What</dt>
          <dd>{LEDGER_KIND[line.kind]}{line.memo ? `: ${line.memo}` : ""}</dd>
          <dt className="text-ink-muted">Amount</dt>
          <dd>{money(line.charged)}</dd>
          <dt className="text-ink-muted">Still open</dt>
          <dd data-testid="charge-open">
            {line.kind === "credit"
              ? "None: a credit comes off the oldest open charges"
              : owing ? money(owing.open) : "Nothing: paid, credited or waived"}
          </dd>
        </dl>
        {owing && (
          <ul className="flex flex-col divide-y divide-line" aria-label="Open charge">
            <OpenChargeRow charge={owing} record={record} />
          </ul>
        )}
      </div>
    );
  }
  const payment = lease.payments.find((p) => p.transfer_id === line.transfer_id);
  if (!payment) {
    return <Notice tone="info">{`${LEDGER_KIND[line.kind]} of ${usDate(line.on)}: ${money(line.paid)}.`}</Notice>;
  }
  return (
    <div className="flex flex-col gap-3">
      {line.kind === "bounced payment" && (
        <p className="text-base">
          {`This payment bounced: ${money((line.paid ?? "").replace(/^-/, ""))} came back on ${usDate(line.on)}.`}
        </p>
      )}
      <table className="w-full text-base" aria-label="The payment">
        <thead>
          <tr>
            <th className="text-left">Received</th>
            <th className="text-right">Amount</th>
            <th><span className="sr-only">Actions</span></th>
          </tr>
        </thead>
        <tbody>
          <PaymentRow payment={payment} record={record} />
        </tbody>
      </table>
    </div>
  );
}
