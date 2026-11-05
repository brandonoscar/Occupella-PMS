import { Inbox } from "lucide-react";
import { useEffect, useState } from "react";
import type { Me } from "../api";
import { ApiError, api } from "../api";
import { Badge } from "../components/Badge";
import type { BadgeTone } from "../components/Badge";
import { Button } from "../components/Button";
import { AskFirst } from "../components/Confirm";
import { EmptyState } from "../components/EmptyState";
import { Field } from "../components/Field";
import { Form } from "../components/Form";
import { Notice } from "../components/Notice";
import { Pager } from "../components/Pager";
import { Said } from "../components/Said";
import { Card, PageHeader, Shell } from "../components/Shell";
import { SidePanel } from "../components/SidePanel";
import { Tabs } from "../components/Tabs";
import { Wide } from "../components/Wide";
import { usDate } from "../money";
import type { Paged } from "../rentals";
import { STAFF_NAV } from "./staffNav";

// A draft as the API sends it (src/pms/api/drafts.py, ADR 0011): what software with an API key
// proposed, by the ids of the company's records, and what became of it.
export type DraftStatus = "waiting" | "approved" | "declined";
export type DraftKind =
  | "email"
  | "notice"
  | "work_order_open"
  | "work_order_assign"
  | "work_order_schedule"
  | "note";

export type DraftRow = {
  id: string;
  kind: DraftKind;
  what: string;
  why: string | null;
  key: string;
  made_at: string;
  status: DraftStatus;
  decided_by: string | null;
  decided_at: string | null;
  edited: boolean | null;
  reason: string | null;
};

// A record a draft names, with its name now; an email's recipient with where it goes today.
type Named = { about: string; id: string; name: string; to: string | null };

type Proposal = {
  kind: DraftKind;
  to?: { about: string; id: string }[];
  subject?: string;
  body?: string;
  template_id?: string;
  lease_ids?: string[];
  dated_on?: string;
  property_id?: string;
  unit_id?: string;
  summary?: string;
  priority?: string;
  description?: string;
  due_on?: string;
  work_order_id?: string;
  vendor_id?: string;
  scheduled_for?: string;
  visit_window?: string;
  about?: string;
  about_id?: string;
};

export type Draft = DraftRow & {
  proposal: Proposal;
  names: Named[];
  acted: Proposal | null;
  result: Record<string, unknown> | null;
};

const STATUS: Record<DraftStatus, { label: string; tone: BadgeTone }> = {
  waiting: { label: "Waiting", tone: "caution" },
  approved: { label: "Approved", tone: "positive" },
  declined: { label: "Declined", tone: "neutral" },
};

// The area of staff work each kind does, as a custom role allows it (src/pms/api/deps.py).
const AREA: Record<DraftKind, string> = {
  email: "email",
  notice: "documents",
  work_order_open: "maintenance",
  work_order_assign: "maintenance",
  work_order_schedule: "maintenance",
  note: "documents",
};

type Show = "waiting" | "decided" | "all";
const TABS: { value: Show; label: string }[] = [
  { value: "waiting", label: "Waiting" },
  { value: "decided", label: "Decided" },
  { value: "all", label: "All" },
];

function detail(failed: unknown): string {
  return failed instanceof ApiError ? failed.detail : String(failed);
}

function StatusBadge({ status }: { status: DraftStatus }) {
  return <Badge tone={STATUS[status].tone}>{STATUS[status].label}</Badge>;
}

// Drafts (ADR 0011): what software with an API key, such as an AI assistant, proposed for staff to
// decide, those waiting first, oldest first. Each opens at /drafts/<id>, where staff approve it,
// change it first or decline it. Nothing is done until someone approves.
export function DraftsPage({ me }: { me: Me }) {
  const [show, setShow] = useState<Show>("waiting");
  const [page, setPage] = useState(1);
  const [drafts, setDrafts] = useState<Paged<DraftRow> | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    setError("");
    api.get<Paged<DraftRow>>(`/api/drafts?show=${show}&page=${page}`).then(setDrafts)
      .catch((failed) => setError(detail(failed)));
  }, [show, page]);

  return (
    <Shell me={me} nav={STAFF_NAV}>
      <PageHeader title="Drafts"
        description="What software with an API key, such as an AI assistant, proposes: emails, notices, work orders and notes. Nothing is done until you approve it, and it is done as you." />
      <Notice tone="error">{error}</Notice>
      <Tabs label="Drafts" tabs={TABS} value={show} onChange={(value) => { setShow(value); setPage(1); }} />
      {drafts !== null && drafts.items.length === 0 && (
        <EmptyState icon={Inbox} title={show === "waiting" ? "No drafts waiting." : "No drafts yet."}
          body="Software holding one of the company's API keys (Settings, AI) proposes drafts here for you to approve or decline." />
      )}
      {drafts !== null && drafts.items.length > 0 && (
        <>
          <Wide>
            <table className="w-full text-base" aria-label="Drafts">
              <thead className="text-sm text-ink-subtle">
                <tr>
                  <th className="text-left">What</th>
                  <th className="text-left">Why</th>
                  <th className="text-left">From</th>
                  <th className="text-left">Proposed</th>
                  <th className="text-left">Status</th>
                </tr>
              </thead>
              <tbody>
                {drafts.items.map((draft) => (
                  <tr key={draft.id} aria-label={draft.what}>
                    <td>
                      <a className="font-medium text-iris hover:text-iris-hover hover:underline" href={`/drafts/${draft.id}`}>
                        {draft.what}
                      </a>
                    </td>
                    <td className="text-ink-muted">{draft.why ?? ""}</td>
                    <td className="text-ink-muted">{draft.key}</td>
                    <td className="text-ink-muted">{usDate(draft.made_at)}</td>
                    <td><StatusBadge status={draft.status} /></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </Wide>
          <Pager page={drafts} onPage={setPage} />
        </>
      )}
    </Shell>
  );
}

function nameOf(draft: Draft, about: string, id: string | undefined): string {
  return draft.names.find((n) => n.about === about && n.id === id)?.name ?? "";
}

// The lines of what a proposal does, as label and value.
function lines(draft: Draft, proposal: Proposal): [string, string][] {
  switch (proposal.kind) {
    case "email":
      return [
        ["Subject", proposal.subject ?? ""],
        ["Message", proposal.body ?? ""],
      ];
    case "notice":
      return [
        ["Template", nameOf(draft, "notice template", proposal.template_id)],
        ["Dated", usDate(proposal.dated_on ?? "")],
        ["For", (proposal.lease_ids ?? []).map((id) => nameOf(draft, "lease", id)).join("; ")],
      ];
    case "work_order_open":
      return [
        ["Where", proposal.unit_id ? nameOf(draft, "unit", proposal.unit_id) : nameOf(draft, "property", proposal.property_id)],
        ["Summary", proposal.summary ?? ""],
        ["Priority", proposal.priority ?? "normal"],
        ...(proposal.description ? [["What is wrong", proposal.description] as [string, string]] : []),
        ...(proposal.due_on ? [["Due", usDate(proposal.due_on)] as [string, string]] : []),
      ];
    case "work_order_assign":
      return [
        ["Work order", nameOf(draft, "work order", proposal.work_order_id)],
        ["Vendor", nameOf(draft, "vendor", proposal.vendor_id)],
      ];
    case "work_order_schedule":
      return [
        ["Work order", nameOf(draft, "work order", proposal.work_order_id)],
        ["Visit", `${usDate(proposal.scheduled_for ?? "")}${proposal.visit_window ? `, ${proposal.visit_window}` : ""}`],
      ];
    case "note":
      return [
        ["About", `${proposal.about ?? ""} ${nameOf(draft, proposal.about ?? "", proposal.about_id)}`],
        ["Note", proposal.body ?? ""],
      ];
  }
}

function Lines({ draft, proposal, label }: { draft: Draft; proposal: Proposal; label: string }) {
  return (
    <dl aria-label={label} className="grid grid-cols-[max-content_1fr] gap-x-6 gap-y-2 text-base">
      {lines(draft, proposal).map(([term, value]) => (
        <div key={term} className="contents">
          <dt className="text-ink-muted">{term}</dt>
          <dd className="whitespace-pre-wrap text-ink">{value}</dd>
        </div>
      ))}
    </dl>
  );
}

// Who an email goes to, each with the address it goes to today, or that they get none.
function Recipients({ draft }: { draft: Draft }) {
  return (
    <ul aria-label="Recipients" className="flex flex-col gap-1 text-base">
      {draft.names.map((person) => (
        <li key={`${person.about}-${person.id}`} className="flex flex-wrap items-center gap-2">
          <span className="font-medium text-ink">{person.name}</span>
          <span className="text-sm text-ink-muted">({person.about})</span>
          {person.to ? (
            <span className="text-sm text-ink-muted">{person.to}</span>
          ) : (
            <Badge tone="neutral">No email on file: gets none</Badge>
          )}
        </li>
      ))}
    </ul>
  );
}

// What approving it asks first: it can't be taken back once done.
function question(draft: Draft): string {
  if (draft.kind === "email") {
    const reached = draft.names.filter((n) => n.to).length;
    return `Email ${reached} ${reached === 1 ? "person" : "people"} now, as you? An email can't be taken back once sent.`;
  }
  return `${draft.what}? It is done now, as you.`;
}

const EDITABLE: DraftKind[] = ["email", "note", "work_order_open", "work_order_schedule"];

// One draft (ADR 0011): what it would do and to whom, why it was proposed and by which key; while
// it waits, Approve (done now, as this staff member, all of it or none), Change and approve, and
// Decline with why; once decided, by whom, and what was done.
export function DraftPage({ me, id }: { me: Me; id: string }) {
  const [draft, setDraft] = useState<Draft | null>(null);
  const [error, setError] = useState("");
  const [said, setSaid] = useState("");
  const [panel, setPanel] = useState<"edit" | "decline" | null>(null);

  function refresh() {
    api.get<Draft>(`/api/drafts/${id}`).then(setDraft).catch((failed) => setError(detail(failed)));
  }
  useEffect(refresh, [id]);

  async function approve(proposal?: Proposal) {
    setError("");
    try {
      const done = await api.post<Draft>(`/api/drafts/${id}/approve`, proposal ? { proposal } : {});
      setDraft(done);
      setPanel(null);
      setSaid(`Approved: ${done.what}.`);
      return true;
    } catch (failed) {
      setError(detail(failed));
      return false;
    }
  }

  const allowed = draft ? (me.areas ?? []).includes(AREA[draft.kind]) : false;
  return (
    <Shell me={me} nav={STAFF_NAV}>
      <Said>{said}</Said>
      {draft === null ? (
        <Notice tone="error">{error}</Notice>
      ) : (
        <>
          <PageHeader title={draft.what}
            description={`Proposed by the API key ${draft.key} on ${usDate(draft.made_at)}.`}
            actions={<StatusBadge status={draft.status} />} />
          <Notice tone="error">{error}</Notice>
          {draft.why && (
            <Card title="Why">
              <p className="text-base whitespace-pre-wrap text-ink">{draft.why}</p>
            </Card>
          )}
          {draft.kind === "email" && (
            <Card title="To">
              <Recipients draft={draft} />
            </Card>
          )}
          <Card title={draft.status === "approved" && draft.edited ? "As proposed" : "What it does"}>
            <Lines draft={draft} proposal={draft.proposal} label="What it does" />
          </Card>
          {draft.status === "approved" && draft.edited && draft.acted && (
            <Card title="As done">
              <Lines draft={draft} proposal={draft.acted} label="As done" />
            </Card>
          )}
          {draft.status === "waiting" ? (
            <div className="flex flex-wrap items-center gap-2">
              {!allowed && (
                <Notice tone="info">Your roles don&apos;t let you do this: you can still decline it.</Notice>
              )}
              {allowed && (
                <AskFirst question={question(draft)} onConfirm={() => approve()}>Approve</AskFirst>
              )}
              {allowed && EDITABLE.includes(draft.kind) && (
                <Button variant="secondary" onClick={() => setPanel("edit")}>Change and approve</Button>
              )}
              <Button variant="secondary" onClick={() => setPanel("decline")}>Decline</Button>
            </div>
          ) : (
            <p className="text-base text-ink-muted">
              {draft.status === "approved"
                ? `Approved by ${draft.decided_by ?? ""} on ${usDate(draft.decided_at)}${draft.edited ? ", after changes" : ""}.`
                : `Declined by ${draft.decided_by ?? ""} on ${usDate(draft.decided_at)}: ${draft.reason ?? ""}`}
            </p>
          )}
          <p><a className="text-sm text-iris hover:text-iris-hover hover:underline" href="/drafts">All drafts</a></p>
          {panel === "edit" && (
            <ChangePanel draft={draft} onClose={() => setPanel(null)} onApprove={approve} />
          )}
          {panel === "decline" && (
            <DeclinePanel id={id} onClose={() => setPanel(null)}
              onDeclined={(done) => { setDraft(done); setPanel(null); setSaid(`Declined: ${done.what}.`); }} />
          )}
        </>
      )}
    </Shell>
  );
}

// Change and approve: its words, or the day of a visit, then done as changed.
function ChangePanel({ draft, onClose, onApprove }: {
  draft: Draft;
  onClose: () => void;
  onApprove: (proposal: Proposal) => Promise<boolean>;
}) {
  const [changed, setChanged] = useState<Proposal>(draft.proposal);
  const set = (field: keyof Proposal) => (event: { target: { value: string } }) =>
    setChanged({ ...changed, [field]: event.target.value });
  const area = "rounded-sm border border-line-strong bg-sunken px-2 py-1.5 text-base text-ink";
  return (
    <SidePanel title="Change and approve" onClose={onClose}>
      <Form className="flex flex-col gap-4" onSubmit={(event) => { event.preventDefault(); return onApprove(changed); }}>
        {draft.kind === "email" && (
          <>
            <Field label="Subject" id="draft-subject" required maxLength={200} value={changed.subject ?? ""} onChange={set("subject")} />
            <label htmlFor="draft-body" className="flex flex-col gap-1.5 text-sm text-ink-muted">
              Message
              <textarea id="draft-body" required maxLength={8000} rows={8} value={changed.body ?? ""} onChange={set("body")} className={area} />
            </label>
          </>
        )}
        {draft.kind === "note" && (
          <label htmlFor="draft-note" className="flex flex-col gap-1.5 text-sm text-ink-muted">
            Note
            <textarea id="draft-note" required maxLength={5000} rows={5} value={changed.body ?? ""} onChange={set("body")} className={area} />
          </label>
        )}
        {draft.kind === "work_order_open" && (
          <>
            <Field label="Summary" id="draft-summary" required maxLength={500} value={changed.summary ?? ""} onChange={set("summary")} />
            <label htmlFor="draft-description" className="flex flex-col gap-1.5 text-sm text-ink-muted">
              What is wrong
              <textarea id="draft-description" maxLength={5000} rows={4} value={changed.description ?? ""}
                onChange={(event) => setChanged({ ...changed, description: event.target.value || undefined })} className={area} />
            </label>
          </>
        )}
        {draft.kind === "work_order_schedule" && (
          <>
            <Field label="Day of the visit" id="draft-day" type="date" required value={changed.scheduled_for ?? ""} onChange={set("scheduled_for")} />
            <Field label="When in the day" id="draft-window" maxLength={60} placeholder="8-10 am" value={changed.visit_window ?? ""}
              onChange={(event) => setChanged({ ...changed, visit_window: event.target.value || undefined })} />
          </>
        )}
        <div className="flex gap-2">
          <Button type="submit">Approve with changes</Button>
          <Button type="button" variant="secondary" onClick={onClose}>Cancel</Button>
        </div>
      </Form>
    </SidePanel>
  );
}

// Decline: why, for whoever proposed it. Nothing is done.
function DeclinePanel({ id, onClose, onDeclined }: { id: string; onClose: () => void; onDeclined: (draft: Draft) => void }) {
  const [reason, setReason] = useState("");
  const [error, setError] = useState("");
  async function decline() {
    setError("");
    try {
      onDeclined(await api.post<Draft>(`/api/drafts/${id}/decline`, { reason }));
    } catch (failed) {
      setError(detail(failed));
    }
  }
  return (
    <SidePanel title="Decline draft" onClose={onClose}>
      <Form className="flex flex-col gap-4" onSubmit={(event) => { event.preventDefault(); return decline(); }}>
        <Field label="Why" id="draft-reason" required maxLength={2000} value={reason} onChange={(event) => setReason(event.target.value)} />
        <p className="text-sm text-ink-subtle">The software that proposed it reads this. Nothing is done.</p>
        <Notice tone="error">{error}</Notice>
        <div className="flex gap-2">
          <Button type="submit">Decline</Button>
          <Button type="button" variant="secondary" onClick={onClose}>Cancel</Button>
        </div>
      </Form>
    </SidePanel>
  );
}
