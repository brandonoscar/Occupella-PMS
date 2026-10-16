import { BookOpen } from "lucide-react";
import { useEffect, useState } from "react";
import type { Me } from "../api";
import { ApiError, api } from "../api";
import { Badge } from "../components/Badge";
import { Button } from "../components/Button";
import { EmptyState } from "../components/EmptyState";
import { Field } from "../components/Field";
import { Notice } from "../components/Notice";
import { Select } from "../components/Select";
import { Card, PageHeader, Shell } from "../components/Shell";
import { Wide } from "../components/Wide";
import type { Journal, JournalEntry, JournalLine } from "../journal";
import { accountLabel, entryTone, entryTotal, mayPost, whyNotPost } from "../journal";
import { day, money, usDate } from "../money";
import { STAFF_NAV } from "./staffNav";
import type { Recorder } from "./useRecord";
import { useRecord } from "./useRecord";
import { Said } from "../components/Said";
import { Form } from "../components/Form";

const MAX_LINES = 20;

// Staff's journal: a correction between the company's ledger accounts that no other page makes
// (money held for the wrong owner, a deposit kept in the operating account). One staff member
// proposes an entry; another who may approve posts it, and only then does money move, through
// the ledger's own rules. Anyone may decline an entry still waiting, saying why.
export function JournalPage({ me }: { me: Me }) {
  const [journal, setJournal] = useState<Journal | null>(null);
  const [error, setError] = useState("");
  // What a step did: said above the list, as the entry it changed moves within it.
  const [done, setDone] = useState("");

  function refresh() {
    api
      .get<Journal>("/api/journal")
      .then(setJournal)
      .catch((failed) => setError(failed instanceof ApiError ? failed.detail : String(failed)));
  }
  useEffect(refresh, []);

  return (
    <Shell me={me} nav={STAFF_NAV}>
      <PageHeader title="Journal entries" />
      {journal && <ProposeCard journal={journal} refresh={refresh} />}
      <Card title="Entries">
        {done && (
          <div className="mb-2">
            <Said>{done}</Said>
          </div>
        )}
        {journal === null ? (
          <Notice tone={error ? "error" : "info"}>{error || "Loading"}</Notice>
        ) : journal.entries.length === 0 ? (
          <EmptyState icon={BookOpen} title="No journal entries yet"
            body="An entry proposed above waits here until another staff member posts or declines it." />
        ) : (
          <ul className="flex flex-col divide-y divide-line">
            {journal.entries.map((entry) => (
              <EntryRow key={entry.id} journal={journal} entry={entry} refresh={refresh} say={setDone} />
            ))}
          </ul>
        )}
      </Card>
    </Shell>
  );
}

function ProposeCard({ journal, refresh }: { journal: Journal; refresh: () => void }) {
  const { said, error, record } = useRecord(refresh);
  const first = journal.accounts[0]?.id ?? "";
  const second = journal.accounts[1]?.id ?? first;
  const blank = (): JournalLine => ({ from_account: first, to_account: second, amount: "" });
  const [on, setOn] = useState(day(new Date()));
  const [memo, setMemo] = useState("");
  const [lines, setLines] = useState<JournalLine[]>([blank()]);
  const change = (n: number, to: Partial<JournalLine>) =>
    setLines(lines.map((line, i) => (i === n ? { ...line, ...to } : line)));
  if (journal.accounts.length < 2) {
    return (
      <Card title="Propose an entry">
        <Notice tone="info">Open a trust bank account and the accounts it holds in Setup first.</Notice>
      </Card>
    );
  }
  return (
    <Card title="Propose an entry">
      <Form
        className="flex flex-col gap-3"
        onSubmit={async (event) => {
          event.preventDefault();
          if (await record("/api/journal", { event_on: on, memo, lines }, () =>
            `Proposed: ${memo}. Another staff member who may approve posts it.`)) {
            setMemo("");
            setLines([blank()]);
          }
        }}
      >
        <div className="flex flex-wrap items-end gap-3">
          <Field label="Dated" id="journal-day" type="date" required value={on}
            onChange={(e) => setOn(e.target.value)} />
          <div className="min-w-64 flex-1">
            <Field label="What it corrects" id="journal-memo" required maxLength={500} value={memo}
              onChange={(e) => setMemo(e.target.value)} />
          </div>
        </div>
        {lines.map((line, n) => (
          <fieldset key={n} className="flex flex-wrap items-end gap-3" aria-label={`Line ${n + 1}`}>
            <Select label={`Line ${n + 1} from`} id={`journal-from-${n}`} value={line.from_account}
              onChange={(e) => change(n, { from_account: e.target.value })}>
              {journal.accounts.map((a) => (
                <option key={a.id} value={a.id}>{a.label}</option>
              ))}
            </Select>
            <Select label={`Line ${n + 1} to`} id={`journal-to-${n}`} value={line.to_account}
              onChange={(e) => change(n, { to_account: e.target.value })}>
              {journal.accounts.map((a) => (
                <option key={a.id} value={a.id}>{a.label}</option>
              ))}
            </Select>
            <Field label={`Line ${n + 1} amount`} id={`journal-amount-${n}`} inputMode="decimal" required
              value={line.amount} onChange={(e) => change(n, { amount: e.target.value })} />
            {lines.length > 1 && (
              <Button type="button" variant="ghost" size="sm"
                onClick={() => setLines(lines.filter((_, i) => i !== n))}>
                Remove line {n + 1}
              </Button>
            )}
          </fieldset>
        ))}
        <div className="flex flex-wrap gap-3">
          {lines.length < MAX_LINES && (
            <Button type="button" variant="secondary" onClick={() => setLines([...lines, blank()])}>
              Add a line
            </Button>
          )}
          <Button type="submit">Propose entry</Button>
        </div>
        <p className="text-sm text-ink-subtle">
          Each line moves money from one account to another. Money moves to another trust bank
          account only with its cash, in the same entry.
        </p>
      </Form>
      <Said>{said}</Said>
      <Notice tone="error">{error}</Notice>
    </Card>
  );
}

function EntryRow({ journal, entry, refresh, say }: {
  journal: Journal;
  entry: JournalEntry;
  refresh: () => void;
  say: (said: string) => void;
}) {
  const { error, record } = useRecord(refresh, say);
  const why = whyNotPost(journal, entry);
  return (
    <li className="flex flex-col gap-2 py-4" aria-label={`${usDate(entry.event_on)} ${entry.memo}`}>
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <p>
          <span className="font-medium">{entry.memo}</span>
          <span className="text-ink-muted"> · dated {usDate(entry.event_on)} · proposed by {entry.proposed_by}</span>
        </p>
        <p className="flex items-center gap-3">
          <span className="font-medium">{money(entryTotal(entry))}</span>
          <Badge tone={entryTone(entry.status)} data-testid="status">{entry.status}</Badge>
        </p>
      </div>
      <Wide>
        <table className="w-full text-left text-sm">
          <thead className="text-ink-muted">
            <tr>
              <th className="py-1 font-medium">From</th>
              <th className="py-1 font-medium">To</th>
              <th className="py-1 text-right font-medium">Amount</th>
            </tr>
          </thead>
          <tbody>
            {entry.lines.map((line, n) => (
              <tr key={n} className="border-t border-line">
                <td className="py-1">{accountLabel(journal, line.from_account)}</td>
                <td className="py-1">{accountLabel(journal, line.to_account)}</td>
                <td className="py-1 text-right">{money(line.amount)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </Wide>
      {entry.status === "posted" && (
        <p className="text-sm text-ink-subtle">Posted by {entry.decided_by}.</p>
      )}
      {entry.status === "declined" && (
        <p className="text-sm text-ink-subtle">Declined by {entry.decided_by}: {entry.reason}.</p>
      )}
      {entry.status === "waiting" && (
        <div className="flex flex-wrap items-end gap-3">
          {mayPost(journal, entry) ? (
            <Button onClick={() =>
              record(`/api/journal/${entry.id}/post`, {}, () => `Posted: ${entry.memo}.`)}>
              Post
            </Button>
          ) : (
            <p className="text-sm text-ink-subtle">{why}</p>
          )}
          <DeclineForm entry={entry} record={record} />
        </div>
      )}
      <Notice tone="error">{error}</Notice>
    </li>
  );
}

// Declining needs a reason, behind a button: most entries are posted.
function DeclineForm({ entry, record }: { entry: JournalEntry; record: Recorder }) {
  const [open, setOpen] = useState(false);
  const [why, setWhy] = useState("");
  if (!open) {
    return <Button variant="secondary" onClick={() => setOpen(true)}>Decline</Button>;
  }
  return (
    <Form
      className="flex flex-wrap items-end gap-3"
      onSubmit={(event) => {
        event.preventDefault();
        record(`/api/journal/${entry.id}/decline`, { reason: why }, () => `Declined: ${entry.memo}.`);
      }}
    >
      <Field label="Why" id={`journal-why-${entry.id}`} required maxLength={1000} value={why}
        onChange={(e) => setWhy(e.target.value)} />
      <Button type="submit" variant="secondary">Decline entry</Button>
    </Form>
  );
}
