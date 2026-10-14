import { useEffect, useState } from "react";
import type { Me } from "../api";
import { ApiError, api } from "../api";
import { Badge } from "../components/Badge";
import { Button } from "../components/Button";
import { Field } from "../components/Field";
import { Notice } from "../components/Notice";
import { Select } from "../components/Select";
import { Card, PageHeader, Shell } from "../components/Shell";
import { money } from "../money";
import type { BankBooks, OpenItem, OpenLine, OpenTransfer, Worksheet } from "../reconcile";
import {
  approver,
  byDay,
  difference,
  figures,
  formatOf,
  itemLabel,
  items,
  lastDayOf,
  lineLabel,
  sum,
  transferLabel,
} from "../reconcile";
import { STAFF_NAV } from "./staffNav";
import { useRecord } from "./useRecord";

// Staff's month-end: import a trust bank account's statements, match their lines to the
// transfers in the books, and once the books lack no line one staff member prepares the period
// and another, who may approve, approves it. The PMS compares what the bank says with the
// books; it moves no money.
export function ReconcilePage({ me }: { me: Me }) {
  const [banks, setBanks] = useState<BankBooks[] | null>(null);
  const [chosen, setChosen] = useState("");
  const [through, setThrough] = useState("");
  // The worksheet, with the trust bank account it was read for.
  const [read, setRead] = useState<{ bank: string; sheet: Worksheet } | null>(null);
  const [error, setError] = useState("");
  const failed = (why: unknown) => setError(why instanceof ApiError ? why.detail : String(why));
  const bank = banks?.find((b) => b.id === chosen) ?? banks?.[0];
  const sheet = read && bank && read.bank === bank.id ? read.sheet : null;

  function refresh() {
    setError("");
    api.get<BankBooks[]>("/api/reconcile").then(setBanks).catch(failed);
  }
  useEffect(refresh, []);
  useEffect(() => {
    if (!bank) return;
    const query = through ? `?through=${through}` : "";
    api
      .get<Worksheet>(`/api/reconcile/${bank.id}${query}`)
      .then((found) => setRead({ bank: bank.id, sheet: found }))
      .catch(failed);
  }, [bank, through]);

  return (
    <Shell me={me} nav={STAFF_NAV}>
      <PageHeader title="Month-end" description="Import bank statements, match the bank to the books, and prepare or approve each month." />
      {banks === null || (bank && sheet === null) ? (
        <Notice tone={error ? "error" : "info"}>{error || "Loading"}</Notice>
      ) : !bank || !sheet ? (
        <Notice tone="info">Add a trust bank account in Setup first.</Notice>
      ) : (
        <>
          <div className="max-w-sm">
            <Select label="Trust bank account" id="reconcile-bank" value={bank.id}
              onChange={(e) => { setChosen(e.target.value); setThrough(""); }}>
              {banks.map((b) => (
                <option key={b.id} value={b.id}>
                  {b.name}
                </option>
              ))}
            </Select>
          </div>
          <Notice tone="error">{error}</Notice>
          <StatementsCard bank={bank} refresh={refresh} />
          {sheet.opening && <OpeningCard bank={bank} sheet={sheet} refresh={refresh} />}
          <WorksheetCard me={me} bank={bank} sheet={sheet} through={through}
            setThrough={setThrough} refresh={refresh} />
          <MatchCard bank={bank} sheet={sheet} refresh={refresh} />
          <ApprovedCard bank={bank} />
        </>
      )}
    </Shell>
  );
}

type BankProps = { bank: BankBooks; refresh: () => void };

function StatementsCard({ bank, refresh }: BankProps) {
  const { said, error, record } = useRecord(refresh);
  const [file, setFile] = useState<{ name: string; content: string } | null>(null);
  const [firstDay, setFirstDay] = useState("");
  const [lastDay, setLastDay] = useState("");
  const [opening, setOpening] = useState("");
  const [closing, setClosing] = useState("");
  return (
    <Card title="Bank statements">
      {bank.statements.length === 0 ? (
        <Notice tone="info">No statement yet. The first one sets where the bank record starts.</Notice>
      ) : (
        <table className="mb-4 text-sm" aria-label="Statements">
          <thead>
            <tr>
              <th className="text-left">Period</th>
              <th className="text-right">Opening</th>
              <th className="text-right">Closing</th>
              <th className="text-right">Lines</th>
              <th className="text-left">File</th>
            </tr>
          </thead>
          <tbody>
            {bank.statements.map((s) => (
              <tr key={s.id}>
                <td>{s.first_day} to {s.last_day}</td>
                <td className="text-right">{money(s.opening_balance)}</td>
                <td className="text-right">{money(s.closing_balance)}</td>
                <td className="text-right">{s.lines}</td>
                <td className="text-ink-muted">{s.source.toUpperCase()}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      <form
        className="flex flex-wrap items-end gap-3"
        onSubmit={async (event) => {
          event.preventDefault();
          if (!file) return;
          const body = {
            bank_account_id: bank.id,
            format: formatOf(file.name),
            content: file.content,
            first_day: firstDay || null,
            last_day: lastDay || null,
            opening_balance: opening || null,
            closing_balance: closing || null,
          };
          const done = (made: { lines: number; first_day: string; last_day: string }) =>
            `Imported ${made.lines} line${made.lines === 1 ? "" : "s"}, ${made.first_day} to ${made.last_day}.`;
          if (await record("/api/reconcile/statements", body, done)) {
            setFile(null);
            setFirstDay("");
            setLastDay("");
            setOpening("");
            setClosing("");
          }
        }}
      >
        <label htmlFor="statement-file" className="flex flex-col gap-1.5 text-sm text-ink-muted">
          Statement file (CSV or OFX)
          <input id="statement-file" type="file" accept=".csv,.ofx,.qfx,text/csv"
            className="text-sm text-ink"
            onChange={async (e) => {
              const chosen = e.target.files?.[0];
              setFile(chosen ? { name: chosen.name, content: await chosen.text() } : null);
            }} />
        </label>
        <Field label="First day" id="statement-first" type="date" value={firstDay}
          onChange={(e) => setFirstDay(e.target.value)} />
        <Field label="Last day" id="statement-last" type="date" value={lastDay}
          onChange={(e) => setLastDay(e.target.value)} />
        <Field label="Opening balance" id="statement-opening" inputMode="decimal" value={opening}
          onChange={(e) => setOpening(e.target.value)} />
        <Field label="Closing balance" id="statement-closing" inputMode="decimal" value={closing}
          onChange={(e) => setClosing(e.target.value)} />
        <Button type="submit" disabled={!file}>Import statement</Button>
      </form>
      <p className="mt-3 text-sm text-ink-subtle">
        {file ? `Ready: ${file.name}.` : "Choose the file first."} A CSV needs its days; by default a
        statement starts the day after the last one, at its closing balance. An OFX file gives its
        own days and closing balance.
      </p>
      <Notice tone="info">{said}</Notice>
      <Notice tone="error">{error}</Notice>
    </Card>
  );
}

function WorksheetCard({
  me,
  bank,
  sheet,
  through,
  setThrough,
  refresh,
}: BankProps & {
  me: Me;
  sheet: Worksheet;
  through: string;
  setThrough: (day: string) => void;
}) {
  const { said, error, record } = useRecord(refresh);
  const shown = figures(sheet.rows);
  const gap = difference(sheet.rows);
  const day = through || sheet.through || "";
  const prepared = bank.prepared;
  const who = prepared && approver(me, prepared);
  return (
    <Card title="Reconciliation">
      {sheet.through === null ? (
        <Notice tone="info">Import a statement to see the reconciliation.</Notice>
      ) : (
        <>
          <div className="mb-4 flex flex-wrap items-end justify-between gap-3">
            <div className="max-w-48">
              <Field label="At the end of" id="reconcile-through" type="date" value={day}
                onChange={(e) => setThrough(e.target.value)} />
            </div>
            {gap !== null &&
              (gap === "0.00" ? (
                <Badge tone="positive">Ties out</Badge>
              ) : (
                <Badge tone="caution">{`Off by ${money(gap)}`}</Badge>
              ))}
          </div>
          <table className="text-sm" aria-label="Figures">
            <tbody>
              {Object.entries(shown).map(([item, amount]) => (
                <tr key={item} className={item.startsWith("difference") ? "font-medium" : ""}>
                  <th scope="row" className="text-left font-normal first-letter:uppercase">{item}</th>
                  <td className="text-right">{money(amount)}</td>
                </tr>
              ))}
            </tbody>
          </table>
          {items(sheet.rows).length > 0 && (
            <table className="mt-4 text-sm" aria-label="Reconciling items">
              <thead>
                <tr>
                  <th className="text-left">Reconciling item</th>
                  <th className="text-left">Detail</th>
                  <th className="text-right">Amount</th>
                </tr>
              </thead>
              <tbody>
                {items(sheet.rows).map((row) => (
                  <tr key={row.line}>
                    <td className="first-letter:uppercase">{row.item}</td>
                    <td className="text-ink-muted">{row.detail}</td>
                    <td className="text-right">{money(row.amount)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
          {prepared && (
            <div
              className="mt-4 flex flex-wrap items-center justify-between gap-3 rounded-md border border-card-edge bg-raised px-4 py-3"
              aria-label="Prepared"
            >
              <span className="text-sm">
                Prepared through {prepared.through} by {prepared.prepared_by}: bank{" "}
                {money(prepared.bank_balance)}, books {money(prepared.book_cash)}.
              </span>
              {who === "may" ? (
                <Button
                  onClick={() =>
                    record<{ id: string }>(`/api/reconcile/${bank.id}/approve`,
                      { preparation_id: prepared.id },
                      () => `Approved through ${prepared.through}; the account is closed through it.`)
                  }
                >
                  Approve through {prepared.through}
                </Button>
              ) : (
                <span className="text-sm text-ink-subtle">
                  {who === "preparer"
                    ? "Another staff member who may approve approves it."
                    : "A staff member who may approve approves it."}
                </span>
              )}
            </div>
          )}
          <div className="mt-4 flex flex-wrap items-center gap-3">
            <Button
              variant={prepared ? "secondary" : "primary"}
              disabled={gap !== "0.00"}
              onClick={() =>
                record<{ id: string }>(`/api/reconcile/${bank.id}/prepare`, { through: day },
                  () => `Prepared through ${day}; another staff member who may approve approves it.`)
              }
            >
              Prepare through {day}
            </Button>
            {gap !== "0.00" && (
              <span className="text-sm text-ink-subtle">
                Lines the books lack come to {money(gap)}: record them and match them first.
              </span>
            )}
          </div>
        </>
      )}
      <Notice tone="info">{said}</Notice>
      <Notice tone="error">{error}</Notice>
    </Card>
  );
}

function MatchCard({ bank, sheet, refresh }: BankProps & { sheet: Worksheet }) {
  const { said, error, record } = useRecord(refresh);
  const [lines, setLines] = useState<string[]>([]);
  const [moved, setMoved] = useState<string[]>([]);
  const [carried, setCarried] = useState<string[]>([]);
  const picked = <T extends { id: string; amount: string }>(all: T[], ids: string[]) =>
    sum(all.filter((x) => ids.includes(x.id)).map((x) => x.amount));
  const lineTotal = picked(sheet.open_lines, lines);
  const movedTotal = sum([picked(sheet.open_transfers, moved), picked(sheet.open_items, carried)]);
  const toggle = (ids: string[], set: (ids: string[]) => void, id: string) =>
    set(ids.includes(id) ? ids.filter((x) => x !== id) : [...ids, id]);
  return (
    <Card title="Match the bank to the books">
      <div className="mb-4 flex flex-wrap items-center gap-3">
        <Button variant="secondary"
          onClick={() => record<{ matched: number }>(`/api/reconcile/${bank.id}/auto-match`, {},
            (done) => `${done.matched} line${done.matched === 1 ? "" : "s"} matched.`)}>
          Match the sure ones
        </Button>
        <span className="text-sm text-ink-subtle">
          Pairs a line with the one transfer of its amount within a week, when there is no other.
        </span>
      </div>
      <div className={`grid gap-6 ${sheet.open_items.length > 0 ? "md:grid-cols-3" : "md:grid-cols-2"}`}>
        <fieldset>
          <legend className="mb-2 text-sm font-medium">Bank lines not matched</legend>
          {sheet.open_lines.length === 0 ? <p className="text-sm text-ink-subtle">None.</p> : (
            <Checks items={sheet.open_lines} label={lineLabel} chosen={lines}
              toggle={(id) => toggle(lines, setLines, id)} />
          )}
        </fieldset>
        <fieldset>
          <legend className="mb-2 text-sm font-medium">Transfers not matched</legend>
          {sheet.open_transfers.length === 0 ? <p className="text-sm text-ink-subtle">None.</p> : (
            <Checks items={sheet.open_transfers} label={transferLabel} chosen={moved}
              toggle={(id) => toggle(moved, setMoved, id)} />
          )}
        </fieldset>
        {sheet.open_items.length > 0 && (
          <fieldset>
            <legend className="mb-2 text-sm font-medium">Carried over, not cleared yet</legend>
            <Checks items={sheet.open_items} label={itemLabel} chosen={carried}
              toggle={(id) => toggle(carried, setCarried, id)} />
          </fieldset>
        )}
      </div>
      <div className="mt-4 flex flex-wrap items-center gap-3">
        <span className="text-sm" data-testid="picked">
          Lines {money(lineTotal)} · Transfers {money(movedTotal)}
        </span>
        <Button
          disabled={lines.length + moved.length + carried.length === 0 || lineTotal !== movedTotal}
          onClick={async () => {
            const body = { line_ids: lines, transfer_ids: moved, cutover_item_ids: carried };
            if (await record(`/api/reconcile/${bank.id}/matches`, body, () => "Matched.")) {
              setLines([]);
              setMoved([]);
              setCarried([]);
            }
          }}
        >
          Match these
        </Button>
      </div>
      {sheet.matches.length > 0 && (
        <table className="mt-6 text-sm" aria-label="Matches">
          <thead>
            <tr>
              <th className="text-left">Bank</th>
              <th className="text-left">Books</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {byDay(sheet.matches).map((m) => (
              <tr key={m.id}>
                <td>{m.lines.map(lineLabel).join("; ") || "No bank line"}</td>
                <td>
                  {[...m.transfers.map(transferLabel), ...m.items.map(itemLabel)].join("; ") ||
                    "No transfer"}
                </td>
                <td className="text-right">
                  <Button variant="ghost" size="sm"
                    onClick={() => record(`/api/reconcile/matches/${m.id}/undo`, {}, () => "Match undone.")}>
                    Undo
                  </Button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      <Notice tone="info">{said}</Notice>
      <Notice tone="error">{error}</Notice>
    </Card>
  );
}

// A migrated account's first statement opens with the bank's balance brought forward: it is
// matched to the opening balances less the items carried over, once the cutover is signed off.
// Once matched, the card says so for good (the refresh after matching must not take the
// confirmation away with the form).
function OpeningCard({ bank, sheet, refresh }: BankProps & { sheet: Worksheet }) {
  const { said, error, record } = useRecord(refresh);
  const opening = sheet.opening;
  const candidates = sheet.open_lines.filter((l) => l.amount === opening?.brought_forward);
  const [line, setLine] = useState("");
  const chosen = candidates.some((l) => l.id === line) ? line : (candidates[0]?.id ?? "");
  if (!opening) return null;
  return (
    <Card title="Opening from the old system">
      <p className="mb-4 text-sm text-ink-muted">
        Opening balances of {money(opening.book_cash)}, less {money(opening.carried)} the bank had
        not cleared: the first statement's balance brought forward should be{" "}
        {money(opening.brought_forward)}.
      </p>
      {opening.matched ? (
        <Notice tone="info">The opening is matched.</Notice>
      ) : !opening.signed_off ? (
        <Notice tone="info">Sign off the cutover at Switching first.</Notice>
      ) : candidates.length === 0 ? (
        <Notice tone="info">No open bank line comes to {money(opening.brought_forward)}.</Notice>
      ) : (
        <div className="flex flex-wrap items-end gap-3">
          <Select label="Balance brought forward" id="opening-line" value={chosen}
            onChange={(e) => setLine(e.target.value)}>
            {candidates.map((l) => (
              <option key={l.id} value={l.id}>
                {lineLabel(l)}
              </option>
            ))}
          </Select>
          <Button onClick={() => record(`/api/reconcile/${bank.id}/opening-match`, { line_id: chosen },
            () => "The opening is matched.")}>
            Match the opening
          </Button>
        </div>
      )}
      {!opening.matched && <Notice tone="info">{said}</Notice>}
      <Notice tone="error">{error}</Notice>
    </Card>
  );
}

function Checks<T extends OpenLine | OpenTransfer | OpenItem>({
  items: all,
  label,
  chosen,
  toggle,
}: {
  items: T[];
  label: (item: T) => string;
  chosen: string[];
  toggle: (id: string) => void;
}) {
  return (
    <ul className="flex flex-col divide-y divide-line rounded-md border border-card-edge text-sm">
      {all.map((item) => (
        <li key={item.id}>
          <label className="flex items-center gap-2.5 px-3 py-2 hover:bg-raised">
            <input type="checkbox" checked={chosen.includes(item.id)} onChange={() => toggle(item.id)} />
            {label(item)}
          </label>
        </li>
      ))}
    </ul>
  );
}

function ApprovedCard({ bank }: { bank: BankBooks }) {
  return (
    <Card title="Approved reconciliations">
      {bank.reconciliations.length === 0 ? (
        <Notice tone="info">None yet.</Notice>
      ) : (
        <table className="text-sm" aria-label="Approved">
          <thead>
            <tr>
              <th className="text-left">Period</th>
              <th className="text-right">Bank</th>
              <th className="text-right">Books</th>
              <th className="text-left">Approved by</th>
            </tr>
          </thead>
          <tbody>
            {bank.reconciliations.map((r) => (
              <tr key={r.id}>
                <td>{r.period_start.slice(0, 10)} to {lastDayOf(r.period_end)}</td>
                <td className="text-right">{money(r.statement_balance)}</td>
                <td className="text-right">{money(r.book_balance)}</td>
                <td>{r.approved_by}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      {bank.closed_through && (
        <p className="mt-3 text-sm text-ink-subtle">
          Closed through {bank.closed_through}: nothing is recorded on or before it.
        </p>
      )}
    </Card>
  );
}
