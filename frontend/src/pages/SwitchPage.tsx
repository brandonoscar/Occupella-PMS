import { useEffect, useState } from "react";
import type { Me } from "../api";
import { ApiError, api } from "../api";
import { Button, LinkButton } from "../components/Button";
import { Field } from "../components/Field";
import { Notice } from "../components/Notice";
import { Select } from "../components/Select";
import { Card, PageHeader, Shell } from "../components/Shell";
import { money } from "../money";
import type { Counted, Cutover, RosterKind } from "../switching";
import {
  BALANCE_COLUMNS,
  ITEM_COLUMNS,
  ROSTER,
  VENDOR_PAYMENT_COLUMNS,
  countedLabel,
  expectedBank,
} from "../switching";
import { STAFF_NAV } from "./staffNav";
import { useRecord } from "./useRecord";
import { Said } from "../components/Said";
import { Form } from "../components/Form";

// The switching tool: a PMC moving from another system brings its roster in from CSV files,
// opens each trust bank account with the balances it held there, lists the checks and deposits
// the bank had not cleared, and signs off the tie-out. What the old system paid each vendor in
// the cutover's year comes over too, for the year's 1099s. Each file goes in whole or not at all.
export function SwitchPage({ me }: { me: Me }) {
  const [cutovers, setCutovers] = useState<Cutover[] | null>(null);
  const [error, setError] = useState("");

  function refresh() {
    api
      .get<Cutover[]>("/api/switching")
      .then(setCutovers)
      .catch((failed) => setError(failed instanceof ApiError ? failed.detail : String(failed)));
  }
  useEffect(refresh, []);

  return (
    <Shell me={me} nav={STAFF_NAV}>
      <PageHeader title="Import from another PMS" />
      <RosterCard />
      <VendorPaymentsCard />
      {cutovers === null ? (
        <Notice tone={error ? "error" : "info"}>{error || "Loading"}</Notice>
      ) : cutovers.length === 0 ? (
        <Notice tone="info">Add a trust bank account in Setup first.</Notice>
      ) : (
        cutovers.map((cutover) => (
          <CutoverCard key={cutover.bank_account_id} cutover={cutover} refresh={refresh} />
        ))
      )}
    </Shell>
  );
}

// A file input that reads its file as text.
function FileField({ id, label, onRead }: { id: string; label: string; onRead: (text: string | null) => void }) {
  return (
    <label htmlFor={id} className="flex flex-col gap-1.5 text-sm text-ink-muted">
      {label}
      <input id={id} type="file" accept=".csv,text/csv" className="text-sm text-ink"
        onChange={async (e) => {
          const file = e.target.files?.[0];
          onRead(file ? await file.text() : null);
        }} />
    </label>
  );
}

function RosterCard() {
  const { said, error, record } = useRecord(() => undefined);
  const [kind, setKind] = useState<RosterKind>("owners");
  const [content, setContent] = useState<string | null>(null);
  const columns = ROSTER.find((r) => r.kind === kind)?.columns;
  return (
    <Card title="Bring the roster in">
      <Form
        className="flex flex-wrap items-end gap-3"
        onSubmit={(event) => {
          event.preventDefault();
          if (content !== null) record<Counted>("/api/switching/import", { kind, content }, countedLabel);
        }}
      >
        <Select label="File of" id="roster-kind" value={kind} onChange={(e) => setKind(e.target.value as RosterKind)}>
          {ROSTER.map((r) => (
            <option key={r.kind} value={r.kind}>
              {r.label}
            </option>
          ))}
        </Select>
        <FileField id="roster-file" label="CSV file" onRead={setContent} />
        <Button type="submit" disabled={content === null}>Import</Button>
      </Form>
      <p className="mt-3 text-sm text-ink-subtle">Columns: {columns}.</p>
      <Said>{said}</Said>
      <Notice tone="error">{error}</Notice>
    </Card>
  );
}

function VendorPaymentsCard() {
  const { said, error, record } = useRecord(() => undefined);
  const [cutover, setCutover] = useState("");
  const [content, setContent] = useState<string | null>(null);
  return (
    <Card title="Vendor payments before the cutover">
      <Form
        className="flex flex-wrap items-end gap-3"
        onSubmit={(event) => {
          event.preventDefault();
          if (content !== null)
            record<Counted>("/api/switching/vendor-payments", { paid_before: cutover, content }, countedLabel);
        }}
      >
        <Field label="Cutover day" id="vendor-payments-cutover" type="date" required value={cutover}
          onChange={(e) => setCutover(e.target.value)} />
        <FileField id="vendor-payments-file" label="Vendor payments (CSV)" onRead={setContent} />
        <Button type="submit" variant="secondary" disabled={content === null}>Carry over</Button>
      </Form>
      <p className="mt-3 text-sm text-ink-subtle">Columns: {VENDOR_PAYMENT_COLUMNS}.</p>
      <Said>{said}</Said>
      <Notice tone="error">{error}</Notice>
    </Card>
  );
}

function CutoverCard({ cutover, refresh }: { cutover: Cutover; refresh: () => void }) {
  const { said, error, record } = useRecord(refresh);
  const [opened, setOpened] = useState("");
  const [book, setBook] = useState("");
  const [balances, setBalances] = useState<string | null>(null);
  const [items, setItems] = useState<string | null>(null);
  const bank = cutover.bank_account_id;
  const expected = expectedBank(cutover.tie_out);
  return (
    <Card title={`Cutover: ${cutover.name}`}>
      {cutover.opened_on === null && !cutover.can_open ? (
        <Notice tone="info">This account has postings or a reconciliation already; it can't open with balances.</Notice>
      ) : cutover.opened_on === null ? (
        <Form
          className="flex flex-wrap items-end gap-3"
          onSubmit={(event) => {
            event.preventDefault();
            const body = { bank_account_id: bank, opened_on: opened, book_cash: book, content: balances };
            record<{ accounts: number }>("/api/switching/opening-balances", body,
              (done) => `Opened with ${done.accounts} balance${done.accounts === 1 ? "" : "s"}.`);
          }}
        >
          <Field label="Cutover date" id={`cutover-${bank}`} type="date" required value={opened}
            onChange={(e) => setOpened(e.target.value)} />
          <Field label="Old system's book cash" id={`book-${bank}`} inputMode="decimal" required
            value={book} onChange={(e) => setBook(e.target.value)} />
          <FileField id={`balances-${bank}`} label="Opening balances (CSV)" onRead={setBalances} />
          <Button type="submit" disabled={balances === null}>Open with balances</Button>
          <p className="w-full text-sm text-ink-subtle">Columns: {BALANCE_COLUMNS}.</p>
        </Form>
      ) : (
        <>
          <dl className="grid grid-cols-[1fr_auto] gap-x-6 gap-y-1 text-sm" aria-label={`Tie-out, ${cutover.name}`}>
            {cutover.tie_out.slice(2).map((row) => (
              <div key={row.line} className="contents">
                <dt className="text-ink-muted">
                  {row.item}
                  {row.detail && row.amount !== null ? `: ${row.detail}` : ""}
                </dt>
                <dd className="text-right">{row.amount !== null ? money(row.amount) : row.detail}</dd>
              </div>
            ))}
          </dl>
          <div className="mt-4 flex flex-wrap items-end gap-3">
            {!cutover.signed_off && (
              <Form
                className="flex flex-wrap items-end gap-3"
                onSubmit={(event) => {
                  event.preventDefault();
                  if (items !== null) {
                    record<Counted>("/api/switching/items", { bank_account_id: bank, content: items },
                      (done) => `${done.added} item${done.added === 1 ? "" : "s"} carried over.`);
                  }
                }}
              >
                <FileField id={`items-${bank}`} label="Checks and deposits not cleared (CSV)" onRead={setItems} />
                <Button type="submit" variant="secondary" disabled={items === null}>Add items</Button>
              </Form>
            )}
            {!cutover.signed_off && (
              <Button onClick={() => record("/api/switching/sign-off", { bank_account_id: bank },
                () => "Signed off. The items carried over are frozen.")}>
                Sign off the tie-out
              </Button>
            )}
            <LinkButton href={`/api/reports/trust_report_cutover?arg=${bank}&format=pdf`}>
              Tie-out (PDF)
            </LinkButton>
          </div>
          {!cutover.signed_off && (
            <p className="mt-3 text-sm text-ink-subtle">
              Items columns: {ITEM_COLUMNS}. The bank's statement should open at {money(expected)}.
            </p>
          )}
        </>
      )}
      <Said>{said}</Said>
      <Notice tone="error">{error}</Notice>
    </Card>
  );
}
