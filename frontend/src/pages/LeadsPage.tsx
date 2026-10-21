import { Contact } from "lucide-react";
import { useEffect, useState } from "react";
import type { Me } from "../api";
import { ApiError, api } from "../api";
import { Badge } from "../components/Badge";
import { Button } from "../components/Button";
import { EmptyState } from "../components/EmptyState";
import { Field } from "../components/Field";
import { Form } from "../components/Form";
import { ListBar } from "../components/ListBar";
import { Notice } from "../components/Notice";
import { Pager } from "../components/Pager";
import { Select } from "../components/Select";
import { PageHeader, Shell } from "../components/Shell";
import { SidePanel } from "../components/SidePanel";
import { Tabs } from "../components/Tabs";
import { Wide } from "../components/Wide";
import type { LeadRow } from "../leads";
import { LEAD_STATUS, askedAbout, showingTime } from "../leads";
import { usDate } from "../money";
import type { Paged } from "../rentals";
import { navigate } from "../router";
import { scopeParams, useScope } from "../scope";
import type { Portfolio } from "../setup";
import { STAFF_NAV } from "./staffNav";
import { useRecord } from "./useRecord";
import { GoLink } from "./NeedsAttention";

type Show = "open" | "closed" | "all";
const TABS: { value: Show; label: string }[] = [
  { value: "open", label: "Open" },
  { value: "closed", label: "Closed" },
  { value: "all", label: "All" },
];
const NONE: Record<Show, string> = {
  open: "No lead is open.",
  closed: "No lead is closed yet.",
  all: "No one has asked yet.",
};

// Leasing → Leads (migration 0088; the founder's review, q083): everyone who asked about renting
// before applying, newest first, a page at a time: open ones, closed (applied or lost) or all,
// maybe of a property group or property. Add lead keeps a new one; each row opens the lead's page,
// where staff set up showings and close it.
export function LeadsPage({ me }: { me: Me }) {
  const [show, setShow] = useState<Show>("open");
  const [scope, setScope] = useScope();
  const [number, setNumber] = useState(1);
  const [page, setPage] = useState<Paged<LeadRow> | null>(null);
  const [error, setError] = useState("");
  const [adding, setAdding] = useState(false);

  useEffect(() => {
    const query = new URLSearchParams({ status: show, page: String(number), ...scopeParams(scope) });
    api.get<Paged<LeadRow>>(`/api/leasing/leads?${query}`).then(setPage)
      .catch((why) => setError(why instanceof ApiError ? why.detail : String(why)));
  }, [show, scope, number]);

  return (
    <Shell me={me} nav={STAFF_NAV}>
      <PageHeader title="Leads" actions={<Button onClick={() => setAdding(true)}>Add lead</Button>} />
      <ListBar scope={scope} onScope={(next) => { setScope(next); setNumber(1); }} />
      <div>
        <Tabs label="Leads" tabs={TABS} value={show} onChange={(next) => { setShow(next); setNumber(1); }} />
        {page === null ? (
          <Notice tone={error ? "error" : "info"}>{error || "Loading"}</Notice>
        ) : page.items.length === 0 ? (
          <EmptyState icon={Contact} title={NONE[show]} />
        ) : (
          <>
            <Wide>
              <table className="w-full text-base">
                <thead>
                  <tr>
                    <th className="text-left">Lead</th>
                    <th className="text-left">Asked about</th>
                    <th className="text-left">Source</th>
                    <th className="text-left">Added</th>
                    <th className="text-left">Next showing</th>
                    <th className="text-right">Showings</th>
                    <th className="text-left">Status</th>
                  </tr>
                </thead>
                <tbody>
                  {page.items.map((lead) => {
                    const badge = LEAD_STATUS[lead.status];
                    return (
                      <tr key={lead.id} aria-label={`Lead ${lead.name}`} className="cursor-pointer hover:bg-raised"
                        onClick={() => navigate(`/leads/${lead.id}`)}>
                        <td><GoLink href={`/leads/${lead.id}`} className="font-medium text-iris hover:text-iris-hover hover:underline">{lead.name}</GoLink></td>
                        <td>{askedAbout(lead)}</td>
                        <td className="text-ink-muted">{lead.source ?? ""}</td>
                        <td className="whitespace-nowrap">{usDate(lead.added_at)}</td>
                        <td className="whitespace-nowrap">{lead.next_showing ? showingTime(lead.next_showing) : ""}</td>
                        <td className="text-right">{lead.showings}</td>
                        <td><Badge tone={badge.tone} data-testid="status">{badge.word}</Badge></td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </Wide>
            <Pager page={page} onPage={setNumber} />
          </>
        )}
      </div>
      {adding && (
        <SidePanel title="Add lead" onClose={() => setAdding(false)}>
          <AddLeadForm />
        </SidePanel>
      )}
    </Shell>
  );
}

// A new lead: a name and an email or a phone, maybe what they asked about and when they'd move in.
// Saved, it opens the lead's page.
function AddLeadForm() {
  const [books, setBooks] = useState<Portfolio | null>(null);
  const [name, setName] = useState("");
  const [email, setEmail] = useState("");
  const [phone, setPhone] = useState("");
  const [property, setProperty] = useState("");
  const [unit, setUnit] = useState("");
  const [source, setSource] = useState("");
  const [moveIn, setMoveIn] = useState("");
  const [note, setNote] = useState("");
  const { error, record } = useRecord(() => undefined);
  useEffect(() => {
    api.get<Portfolio>("/api/setup").then(setBooks).catch(() => setBooks(null));
  }, []);
  const units = books?.properties.find((p) => p.id === property)?.units ?? [];
  const optional = (value: string) => value.trim() || null;
  return (
    <Form className="flex flex-col gap-3" onSubmit={(event) => {
      event.preventDefault();
      const body = {
        name, email: optional(email), phone: optional(phone), property_id: property || null, unit_id: unit || null,
        source: optional(source), move_in_on: moveIn || null, note: optional(note),
      };
      void record<{ id: string }>("/api/leasing/leads", body, (made) => {
        navigate(`/leads/${made.id}`);
        return `Added ${name.trim()}.`;
      });
    }}>
      <Field label="Name" id="lead-name" required value={name} onChange={(e) => setName(e.target.value)} />
      <Field label="Email" id="lead-email" type="email" value={email} onChange={(e) => setEmail(e.target.value)} />
      <Field label="Phone" id="lead-phone" value={phone} onChange={(e) => setPhone(e.target.value)} />
      <p className="text-sm text-ink-subtle">An email or a phone, to reach them.</p>
      <Select label="Property asked about" id="lead-property" value={property}
        onChange={(e) => { setProperty(e.target.value); setUnit(""); }}>
        <option value="">None in particular</option>
        {books?.properties.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
      </Select>
      {units.length > 0 && (
        <Select label="Unit asked about" id="lead-unit" value={unit} onChange={(e) => setUnit(e.target.value)}>
          <option value="">Any unit</option>
          {units.map((u) => <option key={u.id} value={u.id}>{u.name}</option>)}
        </Select>
      )}
      <Field label="Source (optional)" id="lead-source" maxLength={60} placeholder="Website, walk-in, a sign..."
        value={source} onChange={(e) => setSource(e.target.value)} />
      <Field label="Move-in day (optional)" id="lead-move-in" type="date" value={moveIn} onChange={(e) => setMoveIn(e.target.value)} />
      <label htmlFor="lead-note" className="flex flex-col gap-1.5 text-sm text-ink-muted">
        What they asked (optional)
        <textarea id="lead-note" rows={3} maxLength={2000} value={note} onChange={(e) => setNote(e.target.value)}
          className="rounded-sm border border-line-strong bg-sunken px-3 py-2 text-base text-ink" />
      </label>
      <div><Button type="submit">Add lead</Button></div>
      <Notice tone="error">{error}</Notice>
    </Form>
  );
}
