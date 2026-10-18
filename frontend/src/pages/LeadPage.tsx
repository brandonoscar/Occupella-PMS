import { useEffect, useState } from "react";
import type { Me } from "../api";
import { ApiError, api } from "../api";
import { Badge } from "../components/Badge";
import { Button } from "../components/Button";
import { Field } from "../components/Field";
import { Form } from "../components/Form";
import { Notice } from "../components/Notice";
import type { RowAction } from "../components/RowMenu";
import { RowMenu } from "../components/RowMenu";
import { Said } from "../components/Said";
import { Select } from "../components/Select";
import { Card, PageHeader, Shell } from "../components/Shell";
import { SidePanel } from "../components/SidePanel";
import type { Lead, ShowingOutcome } from "../leads";
import { LEAD_STATUS, OUTCOME, askedAbout, showingTime, startsAt } from "../leads";
import { usDate } from "../money";
import type { Portfolio } from "../setup";
import { GoLink } from "./NeedsAttention";
import { STAFF_NAV } from "./staffNav";
import type { Recorder } from "./useRecord";
import { useRecord } from "./useRecord";

const LINK = "font-medium text-iris hover:text-iris-hover hover:underline";
type Step = "showing" | "close";
type Member = { staff_id: string; name: string };

// A lead's page (migration 0088): how to reach them, what they asked about, their showings (each
// one's ⋯ menu records how it went) and, while open, Schedule showing and Close lead.
export function LeadPage({ me, id }: { me: Me; id: string }) {
  const [found, setFound] = useState<Lead | null>(null);
  const [error, setError] = useState("");
  const [step, setStep] = useState<Step | null>(null);
  const [runs, setRuns] = useState(0);
  const { said, error: refused, record } = useRecord(() => setRuns((n) => n + 1));

  useEffect(() => {
    api.get<Lead>(`/api/leasing/leads/${id}`).then(setFound)
      .catch((why) => setError(why instanceof ApiError ? why.detail : String(why)));
  }, [id, runs]);

  const recordThenClose: Recorder = async (path, body, done) => {
    const recorded = await record(path, body, done);
    if (recorded) setStep(null);
    return recorded;
  };

  if (found === null) {
    return (
      <Shell me={me} nav={STAFF_NAV}>
        <PageHeader title="Lead" />
        <Notice tone={error ? "error" : "info"}>{error || "Loading"}</Notice>
      </Shell>
    );
  }
  const badge = LEAD_STATUS[found.status];
  const open = found.status === "open";
  return (
    <Shell me={me} nav={STAFF_NAV}>
      <PageHeader title={found.name} actions={open && (
        <div className="flex flex-wrap items-center gap-2">
          <Button onClick={() => setStep("showing")}>Schedule showing</Button>
          <Button variant="secondary" onClick={() => setStep("close")}>Close lead</Button>
        </div>
      )} />
      <p className="flex flex-wrap items-center gap-3 text-base text-ink-muted">
        <GoLink href="/leads" className={LINK}>Leads</GoLink>
        <Badge tone={badge.tone} data-testid="status">{badge.word}</Badge>
      </p>
      <Said>{said}</Said>
      {step === null && <Notice tone="error">{refused}</Notice>}
      {found.closing && (
        <Notice tone="info">
          {found.closing.outcome === "applied" ? "They applied" : "Lost"}
          {found.closing.reason ? `: ${found.closing.reason}` : ""}. Closed {usDate(found.closing.closed_at)}
          {found.closing.closed_by ? ` by ${found.closing.closed_by}` : ""}.
        </Notice>
      )}
      <Card title="Lead">
        <dl className="grid grid-cols-2 gap-x-6 gap-y-2 text-base md:grid-cols-3" aria-label="Lead details">
          <Item label="Email">{found.email ?? ""}</Item>
          <Item label="Phone">{found.phone ?? ""}</Item>
          <Item label="Asked about">{askedAbout(found) || "Nothing in particular"}</Item>
          <Item label="Source">{found.source ?? ""}</Item>
          <Item label="Moving in">{usDate(found.move_in_on)}</Item>
          <Item label="Added">{usDate(found.added_at)}{found.added_by ? ` by ${found.added_by}` : ""}</Item>
        </dl>
        {found.note && <p className="mt-3 whitespace-pre-line text-base text-ink-secondary">{found.note}</p>}
      </Card>
      <Card title="Showings">
        {found.showing_list.length === 0 ? (
          <p className="text-sm text-ink-subtle">No showing yet.</p>
        ) : (
          <table className="w-full text-base" aria-label="Showings">
            <thead>
              <tr>
                <th className="text-left">When</th>
                <th className="text-left">Unit</th>
                <th className="text-left">Shown by</th>
                <th className="text-left">How it went</th>
                <th><span className="sr-only">Actions</span></th>
              </tr>
            </thead>
            <tbody>
              {found.showing_list.map((showing) => {
                const when = showingTime(showing.starts_at);
                const went = (outcome: ShowingOutcome): RowAction => ({
                  label: OUTCOME[outcome].word,
                  onSelect: () => record(`/api/leasing/showings/${showing.id}/outcome`, { outcome },
                    () => `The ${when} showing: ${OUTCOME[outcome].word.toLowerCase()}.`),
                });
                return (
                  <tr key={showing.id} aria-label={`Showing ${when}`}>
                    <td className="whitespace-nowrap">{when}</td>
                    <td>{showing.unit}, {showing.property}</td>
                    <td>{showing.shown_by ?? ""}</td>
                    <td>
                      {showing.outcome && <Badge tone={OUTCOME[showing.outcome].tone}>{OUTCOME[showing.outcome].word}</Badge>}
                      {showing.note && <span className="ml-2 text-ink-muted">{showing.note}</span>}
                    </td>
                    <td className="text-right">
                      {showing.outcome === null && (
                        <RowMenu label={`Showing ${when}`} actions={[went("shown"), went("no_show"), went("cancelled")]} />
                      )}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        )}
      </Card>
      {step !== null && (
        <SidePanel title={step === "showing" ? "Schedule showing" : "Close lead"} onClose={() => setStep(null)}>
          <p className="text-base text-ink">{found.name}</p>
          {step === "showing"
            ? <ShowingForm lead={found} record={recordThenClose} />
            : <CloseForm lead={found} record={recordThenClose} />}
          <Notice tone="error">{refused}</Notice>
        </SidePanel>
      )}
    </Shell>
  );
}

// A showing of one of the company's units, on a day at a time (the viewer's clock), maybe by a
// staff member; the unit the lead asked about comes first.
function ShowingForm({ lead, record }: { lead: Lead; record: Recorder }) {
  const [books, setBooks] = useState<Portfolio | null>(null);
  const [staff, setStaff] = useState<Member[]>([]);
  const [unit, setUnit] = useState(lead.unit_id ?? "");
  const [day, setDay] = useState("");
  const [time, setTime] = useState("");
  const [by, setBy] = useState("");
  useEffect(() => {
    api.get<Portfolio>("/api/setup").then(setBooks).catch(() => setBooks(null));
    api.get<Member[]>("/api/staff").then(setStaff).catch(() => setStaff([]));
  }, []);
  return (
    <Form className="flex flex-col gap-3" onSubmit={(event) => {
      event.preventDefault();
      const at = startsAt(day, time);
      void record("/api/leasing/leads/" + lead.id + "/showings", { unit_id: unit, starts_at: at, shown_by: by || null },
        () => `Showing on ${showingTime(at)}.`);
    }}>
      <Select label="Unit" id="showing-unit" required value={unit} onChange={(e) => setUnit(e.target.value)}>
        <option value="">Pick a unit</option>
        {books?.properties.map((p) => (
          <optgroup key={p.id} label={p.name}>
            {p.units.map((u) => <option key={u.id} value={u.id}>{u.name}, {p.name}</option>)}
          </optgroup>
        ))}
      </Select>
      <Field label="Day" id="showing-day" type="date" required value={day} onChange={(e) => setDay(e.target.value)} />
      <Field label="Time" id="showing-time" type="time" required value={time} onChange={(e) => setTime(e.target.value)} />
      <Select label="Shown by (optional)" id="showing-by" value={by} onChange={(e) => setBy(e.target.value)}>
        <option value="">Nobody named</option>
        {staff.map((s) => <option key={s.staff_id} value={s.staff_id}>{s.name}</option>)}
      </Select>
      <div><Button type="submit">Schedule</Button></div>
    </Form>
  );
}

// Closes the lead, once: they applied, or the company lost them (and maybe why).
function CloseForm({ lead, record }: { lead: Lead; record: Recorder }) {
  const [outcome, setOutcome] = useState<"applied" | "lost">("applied");
  const [reason, setReason] = useState("");
  return (
    <Form className="flex flex-col gap-3" onSubmit={(event) => {
      event.preventDefault();
      void record(`/api/leasing/leads/${lead.id}/close`, { outcome, reason: reason.trim() || null },
        () => `Closed ${lead.name}: ${outcome === "applied" ? "they applied" : "lost"}.`);
    }}>
      <Select label="Outcome" id="close-outcome" value={outcome}
        onChange={(e) => setOutcome(e.target.value as "applied" | "lost")}>
        <option value="applied">They applied</option>
        <option value="lost">Lost them</option>
      </Select>
      <Field label="Why (optional)" id="close-reason" maxLength={500} value={reason} onChange={(e) => setReason(e.target.value)} />
      <div><Button type="submit">Close lead</Button></div>
    </Form>
  );
}

function Item({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div>
      <dt className="text-sm text-ink-muted">{label}</dt>
      <dd>{children}</dd>
    </div>
  );
}
