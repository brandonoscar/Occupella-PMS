import { useEffect, useState } from "react";
import type { Me } from "../api";
import { ApiError, api } from "../api";
import { Badge } from "../components/Badge";
import { Button, LinkButton } from "../components/Button";
import { useConfirm } from "../components/Confirm";
import { Field } from "../components/Field";
import { Notice } from "../components/Notice";
import { Select } from "../components/Select";
import { Card, PageHeader, Shell } from "../components/Shell";
import { day, money, usDate } from "../money";
import { MAX_BYTES, titleOf, toBase64, typeOf } from "../documents";
import type { Fee, MoveOut, StatementSent } from "../moveout";
import {
  chargeLabel,
  feeLabel,
  hasEnded,
  leaseLabel,
  moveOutStatus,
  needsReceipt,
  preview,
  returnBy,
  sentLine,
  statementPath,
} from "../moveout";
import type { Recorder } from "./useRecord";
import { sharesLine } from "../rentals";
import { InspectionsPanel } from "./InspectionsPanel";
import { STAFF_NAV } from "./staffNav";
import { useRecord } from "./useRecord";
import { Said } from "../components/Said";
import { Form } from "../components/Form";

// Staff's move-outs: end the lease, inspect it on the way in and out, charge what the tenants
// owe for cleaning or damage with its receipts, and return their deposits by the day they are
// due back (the strictest deadline found, migration 0120). The return keeps what the lease owes
// for the owner and gives the rest back; each tenant's statement is a letter with the receipts
// and move-out photos, downloaded or emailed. It records what the bank paid; the PMS moves no
// money itself.
export function MoveOutPage({ me }: { me: Me }) {
  const [leases, setLeases] = useState<MoveOut[] | null>(null);
  const [error, setError] = useState("");

  function refresh() {
    api
      .get<MoveOut[]>("/api/move-out")
      .then(setLeases)
      .catch((failed) => setError(failed instanceof ApiError ? failed.detail : String(failed)));
  }
  useEffect(refresh, []);

  return (
    <Shell me={me} nav={STAFF_NAV}>
      <PageHeader title="Move-outs" />
      {leases === null ? (
        <Notice tone={error ? "error" : "info"}>{error || "Loading"}</Notice>
      ) : leases.length === 0 ? (
        <Notice tone="info">No lease holds a security deposit.</Notice>
      ) : (
        <Card title="Leases holding a deposit">
          <ul className="flex flex-col divide-y divide-line">
            {leases.map((lease) => (
              <LeaseRow key={lease.lease_id} lease={lease} refresh={refresh} />
            ))}
          </ul>
        </Card>
      )}
    </Shell>
  );
}

function LeaseRow({ lease, refresh }: { lease: MoveOut; refresh: () => void }) {
  const { said, error, record } = useRecord(refresh);
  const { ask, prompt } = useConfirm();
  const today = day(new Date());
  const id = lease.lease_id;
  const [ends, setEnds] = useState(lease.ends_on ?? "");
  const [amount, setAmount] = useState("");
  const [memo, setMemo] = useState("");
  const [on, setOn] = useState(today);
  const [owner, setOwner] = useState(lease.owner_accounts[0]?.id ?? "");
  // Kept money goes to the property's owners by their shares (migration 0094); without shares,
  // staff choose the account when there are several.
  const shares = lease.owner_accounts.filter((a) => a.percent !== null);
  const choose = shares.length === 0 && lease.owner_accounts.length > 1;
  const [request] = useState(() => crypto.randomUUID());
  const next = preview(lease);
  const ended = hasEnded(lease, today);
  const status = moveOutStatus(lease, today);
  const due = returnBy(lease, today);
  // The end and a damage charge: in view once the lease has ended, folded away while it runs.
  const endAndCharge = (
    <>
      <Form
        className="flex flex-wrap items-end gap-3"
        onSubmit={(event) => {
          event.preventDefault();
          ask(`End ${lease.property}, ${lease.unit}'s lease on ${usDate(ends)}? Rent is charged only through that day.`,
            () => record(`/api/move-out/${id}/end`, { ends_on: ends }, () => `The lease ends on ${usDate(ends)}.`),
            "End the lease");
        }}
      >
        <Field label="Last day" id={`ends-${id}`} type="date" required value={ends}
          onChange={(e) => setEnds(e.target.value)} />
        <Button type="submit" variant="secondary"
          says="Sets this last day; rent is charged only through it. No fee is charged and the deposit isn't returned yet.">
          End the lease
        </Button>
      </Form>
      <Form
        className="flex flex-wrap items-end gap-3"
        onSubmit={async (event) => {
          event.preventDefault();
          const body = { amount, due_on: lease.ends_on ?? today, memo };
          if (await record(`/api/move-out/${id}/deductions`, body, () => `Charged ${money(amount)}: ${memo.trim()}.`)) {
            setAmount("");
            setMemo("");
          }
        }}
      >
        <Field label="Cleaning or damage" id={`memo-${id}`} required value={memo}
          onChange={(e) => setMemo(e.target.value)} />
        <Field label="Amount" id={`amount-${id}`} inputMode="decimal" required value={amount}
          onChange={(e) => setAmount(e.target.value)} />
        <Button type="submit" variant="secondary"
          says={`Charges the tenants this fee, due ${usDate(lease.ends_on ?? today)}. No money moves now; returning the deposit keeps what the lease owes.`}>
          Charge it
        </Button>
      </Form>
    </>
  );
  return (
    <li>
      <section aria-label={leaseLabel(lease)} className="flex flex-col gap-3 py-4">
        <div className="flex flex-wrap items-baseline justify-between gap-2">
          <p>
            <span className="font-medium">{lease.property}, {lease.unit}</span>
            <span className="text-ink-muted"> · {lease.tenants.join(", ")}</span>
          </p>
          <span className="flex flex-wrap gap-2">
            {due && <Badge tone={due.tone}>{due.label}</Badge>}
            <Badge tone={status.tone}>{status.label}</Badge>
          </span>
        </div>
        <p className="text-sm text-ink-muted">
          Lease {usDate(lease.starts_on)} to {lease.ends_on ? usDate(lease.ends_on) : "month to month"} · owes {money(lease.owed)}
          {Number(lease.carried) > 0 && ` and ${money(lease.carried)} at the home they moved from`} ·
          deposits held {money(lease.held)}
        </p>
        <InspectionsPanel leaseId={id} label={leaseLabel(lease)} />
        {lease.fees.length > 0 && <FeeReceipts lease={lease} record={record} />}
        {lease.settled ? (
          <div className="flex flex-col gap-3">
            <p className="text-sm" aria-label={`Returned, ${leaseLabel(lease)}`}>
              Returned on {usDate(lease.settled.returned_on)}: kept {money(lease.settled.kept)} of{" "}
              {money(lease.settled.held)}, {money(lease.settled.returned)} back to the tenants.
            </p>
            <div className="flex flex-wrap items-center gap-3">
              {lease.tenants.map((name, i) => (
                <LinkButton key={lease.tenant_ids[i]} href={statementPath(id, lease.tenant_ids[i] ?? "")}>
                  Statement for {name} (PDF)
                </LinkButton>
              ))}
              <Button variant="secondary" onClick={() =>
                record<StatementSent>(`/api/move-out/${id}/statement/send`, {}, sentLine)}
                says={`Emails ${lease.tenants.join(", ")} their statement at the email on file. Each press emails it again.`}>
                Email the statement
              </Button>
            </div>
            <p className="text-sm text-ink-subtle">
              Each statement is a letter to the tenant&apos;s forwarding address (their mailing address on
              their page) with the receipts and move-out photos. Mail it where your state asks.
            </p>
          </div>
        ) : (
          <>
            {lease.open_charges.length > 0 && (
              <ul className="flex flex-col gap-1 text-sm" aria-label={`Owed, ${leaseLabel(lease)}`}>
                {lease.open_charges.map((c) => (
                  <li key={c.id}>
                    {chargeLabel(c)}{c.home ? ` (${c.home})` : ""}: {money(c.open)}
                  </li>
                ))}
              </ul>
            )}
            {ended ? (
              endAndCharge
            ) : (
              <details>
                <summary>Move-out for {lease.unit}</summary>
                <div className="mt-3 flex flex-col gap-4">
                  {endAndCharge}
                  <p className="text-sm text-ink-subtle">The deposit goes back once the lease has ended.</p>
                </div>
              </details>
            )}
            {ended ? (
              <Form
                className="flex flex-wrap items-end gap-3"
                onSubmit={(event) => {
                  event.preventDefault();
                  const body = {
                    request_id: request,
                    on,
                    owner_account: choose ? owner : null,
                  };
                  ask(`Return the deposit on ${usDate(on)}: keep ${money(next.kept)} for what the lease owes and`
                    + ` pay ${money(next.returned)} back to the tenants? This records what you paid at the bank;`
                    + " it sends no money or email.", () =>
                    record<{ returned: string }>(`/api/move-out/${id}/return`, body,
                      (done) => `Returned ${money(done.returned)} to the tenants; the rest paid what the lease owed.`));
                }}
              >
                <Field label="Paid on" id={`on-${id}`} type="date" required value={on}
                  onChange={(e) => setOn(e.target.value)} />
                {shares.length > 0 && (
                  <p className="basis-full text-sm text-ink-muted" data-testid="kept-by-ownership">
                    Kept money goes to the owners by their shares: {sharesLine(shares)}
                  </p>
                )}
                {choose && (
                  <Select label="Kept money to" id={`owner-${id}`} value={owner}
                    onChange={(e) => setOwner(e.target.value)}>
                    {lease.owner_accounts.map((a) => (
                      <option key={a.id} value={a.id}>
                        {a.owner}
                      </option>
                    ))}
                  </Select>
                )}
                <Button type="submit"
                  says={`Keeps ${money(next.kept)} for what the lease owes; ${money(next.returned)} goes back. A lease's deposit is returned once.`}>
                  Return the deposit
                </Button>
              </Form>
            ) : null}
            {prompt}
          </>
        )}
        <Said>{said}</Said>
        <Notice tone="error">{error}</Notice>
      </section>
    </li>
  );
}

// The fees a deposit may keep, each with its receipts: a link to each, a way to add one, and a
// badge where a fee over $125 has none (California asks for them with the statement).
function FeeReceipts({ lease, record }: { lease: MoveOut; record: Recorder }) {
  return (
    <ul className="flex flex-col gap-2 text-sm" aria-label={`Fees and receipts, ${leaseLabel(lease)}`}>
      {lease.fees.map((fee) => (
        <FeeRow key={fee.id} leaseId={lease.lease_id} fee={fee} record={record} />
      ))}
    </ul>
  );
}

function FeeRow({ leaseId, fee, record }: { leaseId: string; fee: Fee; record: Recorder }) {
  const [file, setFile] = useState<{ name: string; type: string; base64: string } | null>(null);
  const [problem, setProblem] = useState("");
  const [picker, setPicker] = useState(0);
  return (
    <li className="flex flex-col gap-1">
      <div className="flex flex-wrap items-center gap-2">
        <span>{feeLabel(fee)}: {money(fee.amount)}</span>
        {needsReceipt(fee) && <Badge tone="caution">Receipt needed</Badge>}
        {fee.receipts.map((r) => (
          <a key={r.id} className="text-link underline" href={`/api/documents/${r.id}/file`}>{r.title}</a>
        ))}
      </div>
      <Form
        className="flex flex-wrap items-end gap-3"
        onSubmit={async (event) => {
          event.preventDefault();
          if (!file) return;
          const body = { title: titleOf(file.name), content_type: file.type, content_base64: file.base64 };
          if (await record(`/api/move-out/${leaseId}/fees/${fee.id}/receipts`, body,
            () => `Kept ${titleOf(file.name)} as a receipt for ${fee.memo ?? "the fee"}.`)) {
            setFile(null);
            setPicker((n) => n + 1);
          }
        }}
      >
        <label htmlFor={`receipt-${fee.id}`} className="flex flex-col gap-1.5 text-sm text-ink-muted">
          Receipt for {fee.memo ?? "the fee"}
          <input key={picker} id={`receipt-${fee.id}`} type="file" accept=".pdf,.jpg,.jpeg,.png"
            className="text-sm text-ink"
            onChange={async (e) => {
              const picked = e.target.files?.[0];
              setProblem("");
              if (!picked) return setFile(null);
              const type = typeOf(picked);
              if (!type) return setProblem("A receipt is a PDF, JPEG or PNG.");
              if (picked.size > MAX_BYTES) return setProblem("A receipt is a file of at most 5 MB.");
              const bytes = new Uint8Array(await picked.arrayBuffer());
              setFile({ name: picked.name, type, base64: toBase64(bytes) });
            }} />
        </label>
        <Button type="submit" variant="secondary" disabled={file === null}
          says="Keeps it with this fee and the lease's files; its tenants can download it in the portal. Nothing is charged.">
          Add receipt
        </Button>
      </Form>
      <Notice tone="error">{problem}</Notice>
    </li>
  );
}
