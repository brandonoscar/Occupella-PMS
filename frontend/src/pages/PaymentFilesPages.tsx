import { FileText } from "lucide-react";
import { useEffect, useState } from "react";
import type { Me } from "../api";
import { ApiError, api } from "../api";
import { Badge } from "../components/Badge";
import type { BadgeTone } from "../components/Badge";
import { Button } from "../components/Button";
import { EmptyState } from "../components/EmptyState";
import { Field } from "../components/Field";
import { Form } from "../components/Form";
import { Notice } from "../components/Notice";
import { Pager } from "../components/Pager";
import { RowMenu } from "../components/RowMenu";
import { Said } from "../components/Said";
import { Select } from "../components/Select";
import { PageHeader, Shell } from "../components/Shell";
import { SidePanel } from "../components/SidePanel";
import { Wide } from "../components/Wide";
import { money, usDate } from "../money";
import type { FileDetail, FileLine, FileSummary, LineStatus, Preview } from "../paymentFiles";
import { cameInLine, named, previewLine } from "../paymentFiles";
import type { LeaseDetail, Paged } from "../rentals";
import { navigate } from "../router";
import { LeaseFinder } from "./LeaseFinder";
import { GoLink } from "./NeedsAttention";
import { STAFF_NAV } from "./staffNav";
import { useRecord } from "./useRecord";

function detail(failed: unknown): string {
  return failed instanceof ApiError ? failed.detail : String(failed);
}

const TONE: Record<LineStatus, BadgeTone> = {
  "to record": "info",
  recorded: "positive",
  waiting: "caution",
  "set aside": "neutral",
};
const WORD: Record<LineStatus, string> = {
  "to record": "To record",
  recorded: "Recorded",
  waiting: "Waiting",
  "set aside": "Skipped",
};

// Rentals → Rent roll → Payment files (the founder's review q035): a bank's or lockbox's CSV file
// of the tenants' payments it received, brought in once. Each line naming one lease is recorded
// to it as Receive payment records one; the rest wait for staff on the file's page.
export function PaymentFilesPage({ me }: { me: Me }) {
  const [page, setPage] = useState<Paged<FileSummary> | null>(null);
  const [number, setNumber] = useState(1);
  const [error, setError] = useState("");
  const [bringing, setBringing] = useState(false);

  useEffect(() => {
    let current = true;
    api.get<Paged<FileSummary>>(`/api/rent/payment-files?page=${number}`)
      .then((answer) => { if (current) setPage(answer); })
      .catch((failed) => { if (current) setError(detail(failed)); });
    return () => { current = false; };
  }, [number]);

  return (
    <Shell me={me} nav={STAFF_NAV}>
      <PageHeader title="Payment files" actions={<Button onClick={() => setBringing(true)}>Bring in a file</Button>} />
      <p className="text-sm text-ink-muted">
        A file of the payments your bank or lockbox received: a Date and Amount column, and each line's Unit (with its
        Property when units share a name) or Payer; Reference and Method if the bank gives them.
      </p>
      {page === null ? (
        <Notice tone={error ? "error" : "info"}>{error || "Loading"}</Notice>
      ) : page.items.length === 0 ? (
        <EmptyState icon={FileText} title="No payment file has come in yet." />
      ) : (
        <>
          <Wide>
            <table className="w-full text-base" aria-label="Payment files">
              <thead>
                <tr>
                  <th className="text-left">Came in</th>
                  <th className="text-left">File</th>
                  <th className="text-left">Brought in by</th>
                  <th className="text-right">Lines</th>
                  <th className="text-right">Recorded</th>
                  <th className="text-right">Waiting</th>
                  <th className="text-right">Skipped</th>
                </tr>
              </thead>
              <tbody>
                {page.items.map((file) => (
                  <tr key={file.id} aria-label={file.file_name} className="cursor-pointer hover:bg-raised"
                    onClick={() => navigate(`/payment-files/${file.id}`)}>
                    <td className="whitespace-nowrap">{usDate(file.came_in.slice(0, 10))}</td>
                    <td onClick={(event) => event.stopPropagation()}>
                      <GoLink href={`/payment-files/${file.id}`} className="font-medium text-iris hover:text-iris-hover hover:underline">
                        {file.file_name}
                      </GoLink>
                    </td>
                    <td>{file.brought_by ?? ""}</td>
                    <td className="text-right">{file.lines}</td>
                    <td className="text-right">{file.recorded} ({money(file.recorded_total)})</td>
                    <td className="text-right">
                      {file.waiting > 0 ? <Badge tone="caution">{file.waiting} waiting</Badge> : "0"}
                    </td>
                    <td className="text-right">{file.set_aside}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </Wide>
          <Pager page={page} onPage={setNumber} />
        </>
      )}
      {bringing && (
        <SidePanel title="Bring in a payment file" onClose={() => setBringing(false)}>
          <BringIn onCame={(file) => navigate(`/payment-files/${file.id}`)} />
        </SidePanel>
      )}
    </Shell>
  );
}

// Choose the file, see what it would do line by line, then bring it in.
function BringIn({ onCame }: { onCame: (file: FileDetail) => void }) {
  const [file, setFile] = useState<{ name: string; content: string } | null>(null);
  const [preview, setPreview] = useState<Preview | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    setPreview(null);
    setError("");
    if (file === null) return;
    let current = true;
    api.post<Preview>("/api/rent/payment-files/preview", { file_name: file.name, content: file.content })
      .then((answer) => { if (current) setPreview(answer); })
      .catch((failed) => { if (current) setError(detail(failed)); });
    return () => { current = false; };
  }, [file]);

  return (
    <Form className="flex flex-col gap-4" onSubmit={async (event) => {
      event.preventDefault();
      if (file === null) return;
      setError("");
      try {
        onCame(await api.post<FileDetail>("/api/rent/payment-files", { file_name: file.name, content: file.content }));
      } catch (failed) {
        setError(detail(failed));
      }
    }}>
      <label htmlFor="payment-file" className="flex flex-col gap-1.5 text-sm text-ink-muted">
        Payment file (CSV)
        <input id="payment-file" type="file" accept=".csv,text/csv" className="text-sm text-ink"
          onChange={async (event) => {
            const chosen = event.target.files?.[0];
            setFile(chosen ? { name: chosen.name, content: await chosen.text() } : null);
          }} />
      </label>
      <Notice tone="error">{error}</Notice>
      {preview !== null && (
        <>
          {preview.came_in !== null && (
            <Notice tone="error">This file came in on {usDate(preview.came_in.slice(0, 10))}; it comes in once.</Notice>
          )}
          <p className="text-sm text-ink" data-testid="preview-summary">{previewLine(preview)}</p>
          <Wide>
            <table className="w-full text-sm" aria-label="What the file will do">
              <thead>
                <tr>
                  <th className="text-left">Line</th>
                  <th className="text-left">Received</th>
                  <th className="text-right">Amount</th>
                  <th className="text-left">Names</th>
                  <th className="text-left">What happens</th>
                </tr>
              </thead>
              <tbody>
                {preview.lines.map((line) => (
                  <tr key={line.line_no}>
                    <td>{line.line_no}</td>
                    <td className="whitespace-nowrap">{usDate(line.received_on)}</td>
                    <td className="text-right">{money(line.amount)}</td>
                    <td>{named(line)}</td>
                    <td>
                      <Badge tone={TONE[line.status]}>{WORD[line.status]}</Badge>{" "}
                      {line.status === "to record" ? line.lease : line.note}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </Wide>
          {preview.came_in === null && (
            <div>
              <Button type="submit"
                says="Records each line marked To record as a payment to its lease and emails its tenants a receipt. The rest wait here.">
                Bring it in
              </Button>
            </div>
          )}
        </>
      )}
    </Form>
  );
}

// A payment file's page: each line recorded (to which lease), waiting (why) or skipped (why).
// A waiting line's ⋯ menu records it to a lease staff pick, or sets it aside.
export function PaymentFilePage({ me, id }: { me: Me; id: string }) {
  const [file, setFile] = useState<FileDetail | null>(null);
  const [error, setError] = useState("");
  const [changes, setChanges] = useState(0);
  const [panel, setPanel] = useState<{ kind: "record" | "aside"; line: FileLine } | null>(null);
  const { said, error: refused, record } = useRecord(() => {
    setChanges((n) => n + 1);
    setPanel(null);
  });

  useEffect(() => {
    let current = true;
    api.get<FileDetail>(`/api/rent/payment-files/${id}`)
      .then((answer) => { if (current) setFile(answer); })
      .catch((failed) => { if (current) setError(detail(failed)); });
    return () => { current = false; };
  }, [id, changes]);

  if (file === null) {
    return (
      <Shell me={me} nav={STAFF_NAV}>
        <PageHeader title="Payment file" />
        <Notice tone={error ? "error" : "info"}>{error || "Loading"}</Notice>
      </Shell>
    );
  }
  return (
    <Shell me={me} nav={STAFF_NAV}>
      <PageHeader title={file.file_name} />
      <p className="text-sm text-ink-muted" data-testid="file-summary">
        Came in {usDate(file.came_in.slice(0, 10))}{file.brought_by ? ` from ${file.brought_by}` : ""}. {cameInLine(file)}
      </p>
      <Said>{said}</Said>
      {panel === null && <Notice tone="error">{refused}</Notice>}
      <Wide>
        <table className="w-full text-base" aria-label="Lines">
          <thead>
            <tr>
              <th className="text-left">Line</th>
              <th className="text-left">Received</th>
              <th className="text-right">Amount</th>
              <th className="text-left">Names</th>
              <th className="text-left">Reference</th>
              <th className="text-left">Status</th>
              <th className="text-left">Lease</th>
              <th><span className="sr-only">Actions</span></th>
            </tr>
          </thead>
          <tbody>
            {file.items.map((line) => (
              <tr key={line.line_no} aria-label={`Line ${line.line_no}`}>
                <td>{line.line_no}</td>
                <td className="whitespace-nowrap">{usDate(line.received_on)}</td>
                <td className="text-right">{money(line.amount)}</td>
                <td>{named(line)}</td>
                <td>{line.reference ?? ""}</td>
                <td><Badge tone={TONE[line.status]}>{WORD[line.status]}</Badge></td>
                <td>
                  {line.status === "recorded" && line.lease_id ? (
                    <GoLink href={`/leases/${line.lease_id}`} className="text-iris hover:text-iris-hover hover:underline">
                      {line.lease}
                    </GoLink>
                  ) : (
                    <span className="text-sm text-ink-muted">{line.note}</span>
                  )}
                  {line.by && line.status !== "waiting" && (
                    <span className="block text-xs text-ink-muted">by {line.by}</span>
                  )}
                </td>
                <td className="text-right">
                  {line.status === "waiting" && (
                    <RowMenu label={`line ${line.line_no}`} actions={[
                      { label: "Record to a lease", onSelect: () => setPanel({ kind: "record", line }) },
                      { label: "Skip", onSelect: () => setPanel({ kind: "aside", line }) },
                    ]} />
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </Wide>
      {panel?.kind === "record" && (
        <SidePanel title={`Record line ${panel.line.line_no}`} onClose={() => setPanel(null)}>
          <RecordLine line={panel.line} record={record} />
          <Notice tone="error">{refused}</Notice>
        </SidePanel>
      )}
      {panel?.kind === "aside" && (
        <SidePanel title={`Skip line ${panel.line.line_no}`} onClose={() => setPanel(null)}>
          <SetAside line={panel.line} record={record} />
          <Notice tone="error">{refused}</Notice>
        </SidePanel>
      )}
    </Shell>
  );
}

type Recorder = ReturnType<typeof useRecord>["record"];

function lineWords(line: FileLine): string {
  return `${money(line.amount)} received ${usDate(line.received_on)}`;
}

// Record a waiting line to a lease: the one it names, if it names one, or another staff find.
function RecordLine({ line, record }: { line: FileLine; record: Recorder }) {
  const [lease, setLease] = useState<LeaseDetail | null>(null);
  const [pastHold, setPastHold] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    if (line.lease_id === null) return;
    let current = true;
    api.get<LeaseDetail>(`/api/leases/${line.lease_id}`)
      .then((answer) => { if (current) setLease(answer); })
      .catch((failed) => { if (current) setError(detail(failed)); });
    return () => { current = false; };
  }, [line.lease_id]);

  const shares = lease?.owners.filter((o) => o.percent !== null) ?? [];
  const choose = lease !== null && shares.length === 0 && lease.owners.length > 1;
  const [account, setAccount] = useState("");

  return (
    <div className="flex flex-col gap-4">
      <p className="text-base text-ink">{lineWords(line)}{named(line) ? `, naming ${named(line)}` : ""}.</p>
      {line.note && <p className="text-sm text-ink-muted">Waiting: {line.note}</p>}
      <Notice tone="error">{error}</Notice>
      {lease === null ? (
        <LeaseFinder id={`find-lease-${line.line_no}`} onLease={setLease} />
      ) : (
        <Form className="flex flex-col gap-3" onSubmit={async (event) => {
          event.preventDefault();
          const body = {
            lease_id: lease.id,
            ...(choose ? { account: account || lease.owners[0]?.account } : {}),
            ...(lease.on_hold && pastHold ? { past_hold: true } : {}),
          };
          await record<FileLine>(`/api/rent/payment-files/lines/${line.id}/record`, body,
            (done) => `Line ${done.line_no}: ${lineWords(done)} recorded to ${done.lease}.`);
        }}>
          <p className="flex flex-wrap items-center justify-between gap-2 text-base">
            <span>{lease.property} · {lease.unit} · {lease.tenants.map((t) => t.name).join(", ")}</span>
            <Button variant="ghost" size="sm" onClick={() => setLease(null)}>Another lease</Button>
          </p>
          <p className="text-sm text-ink-muted">Balance due {money(lease.owed)}</p>
          {choose && (
            <Select label="Owner's account" id={`line-account-${line.line_no}`}
              value={account || lease.owners[0]?.account} onChange={(e) => setAccount(e.target.value)}>
              {lease.owners.map((o) => <option key={o.account} value={o.account}>{o.owner}</option>)}
            </Select>
          )}
          {lease.on_hold && (
            <label className="flex items-center gap-2 text-sm text-ink">
              <input type="checkbox" checked={pastHold} onChange={(e) => setPastHold(e.target.checked)} />
              Accept this payment past the payment hold
            </label>
          )}
          <div>
            <Button type="submit"
              says="Records it as a payment to this lease, as Receive payment does; each tenant with an email on file gets a receipt.">
              Record {money(line.amount)} to this lease
            </Button>
          </div>
        </Form>
      )}
    </div>
  );
}

function SetAside({ line, record }: { line: FileLine; record: Recorder }) {
  const [reason, setReason] = useState("");
  return (
    <Form className="flex flex-col gap-3" onSubmit={async (event) => {
      event.preventDefault();
      await record<FileLine>(`/api/rent/payment-files/lines/${line.id}/set-aside`, { reason },
        (done) => `Line ${done.line_no} is skipped; it records nothing.`);
    }}>
      <p className="text-base text-ink">{lineWords(line)}.</p>
      <Field label="Why" id={`aside-${line.line_no}`} required maxLength={500} value={reason}
        onChange={(event) => setReason(event.target.value)} />
      <div><Button type="submit" says="Records nothing for it, now or later; why is kept with the line.">Skip this line</Button></div>
    </Form>
  );
}
