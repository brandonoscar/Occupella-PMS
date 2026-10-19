import { Wrench } from "lucide-react";
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
import { Said } from "../components/Said";
import { Select } from "../components/Select";
import { Card, PageHeader, Shell } from "../components/Shell";
import { SidePanel } from "../components/SidePanel";
import { Wide } from "../components/Wide";
import { day, money, usDate } from "../money";
import type { Paged } from "../rentals";
import { navigate } from "../router";
import { scopeParams, useScope } from "../scope";
import type { Named, Portfolio } from "../setup";
import type { OnCall } from "../maintenance";
import type {
  Entry, Priority, RecurringWorkOrder, WorkOrder, WorkOrderRow, WorkOrderStatus,
} from "../workOrders";
import { ENTRY, PRIORITY, STATUS, daysLabel, recurringLine, whereLabel } from "../workOrders";
import { RecordBillForm } from "./BillForms";
import { FilesCard } from "./FilesCard";
import { MaintenanceCard } from "./MaintenanceCard";
import { GoLink } from "./NeedsAttention";
import { STAFF_NAV } from "./staffNav";
import type { Recorder } from "./useRecord";
import { useRecord } from "./useRecord";

const LINK = "font-medium text-iris hover:text-iris-hover hover:underline";
const TEXTAREA = "rounded-sm border border-line-strong bg-sunken px-3 py-2 text-base text-ink";
const FILTER = "h-9 rounded-sm border border-line-strong bg-canvas px-2 text-sm text-ink";

function detail(failed: unknown): string {
  return failed instanceof ApiError ? failed.detail : String(failed);
}

// Which work orders the list shows: the open ones, those of one status, or all.
type Shown = "open" | "all" | WorkOrderStatus;

// The list's query for what it shows: the open ones, all, or one status among all.
export function shownQuery(shown: Shown): Record<string, string> {
  if (shown === "open" || shown === "all") return { show: shown };
  return { show: "all", status: shown };
}

// Maintenance → Work orders (ADR 0005, ADR 0006, Buildium's): every work order a page at a time,
// newest first, narrowed by status, property group and vendor: its number, where, what, vendor,
// priority, status, due day, days open and when it last changed; the row opens its page. New work
// order opens one; On call says who is emailed when a tenant files an emergency; tenants'
// requests are below.
export function WorkOrdersPage({ me }: { me: Me }) {
  const [shown, setShown] = useState<Shown>("open");
  const [scope, setScope] = useScope();
  const [vendor, setVendor] = useState("");
  const [vendors, setVendors] = useState<Named[]>([]);
  const [number, setNumber] = useState(1);
  const [page, setPage] = useState<Paged<WorkOrderRow> | null>(null);
  const [error, setError] = useState("");
  const [opening, setOpening] = useState(false);
  const [onCall, setOnCall] = useState(false);
  const [recurring, setRecurring] = useState(false);

  useEffect(() => {
    api.get<Named[]>("/api/maintenance/vendors").then(setVendors).catch(() => setVendors([]));
  }, []);
  useEffect(() => {
    const query = new URLSearchParams({ ...shownQuery(shown), page: String(number), ...scopeParams(scope) });
    if (vendor) query.set("vendor", vendor);
    api.get<Paged<WorkOrderRow>>(`/api/work-orders?${query}`).then(setPage).catch((failed) => setError(detail(failed)));
  }, [shown, scope, vendor, number]);
  const restart = <T,>(set: (value: T) => void) => (value: T) => { set(value); setNumber(1); };

  return (
    <Shell me={me} nav={STAFF_NAV}>
      <PageHeader title="Work orders" actions={
        <div className="flex flex-wrap items-center gap-2">
          <Button variant="secondary" onClick={() => setOnCall(true)}>On call</Button>
          <Button variant="secondary" onClick={() => setRecurring(true)}>Recurring</Button>
          <Button onClick={() => setOpening(true)}>New work order</Button>
        </div>
      } />
      <ListBar scope={scope} onScope={restart(setScope)}>
        <select aria-label="Status" value={shown} onChange={(e) => restart(setShown)(e.target.value as Shown)} className={FILTER}>
          <option value="open">Open</option>
          <option value="assigned">Assigned</option>
          <option value="scheduled">Scheduled</option>
          <option value="completed">Completed</option>
          <option value="cancelled">Cancelled</option>
          <option value="all">All statuses</option>
        </select>
        <select aria-label="Vendor" value={vendor} onChange={(e) => restart(setVendor)(e.target.value)} className={FILTER}>
          <option value="">All vendors</option>
          {vendors.map((v) => <option key={v.id} value={v.id}>{v.name}</option>)}
        </select>
      </ListBar>
      <Notice tone="error">{error}</Notice>
      {page === null ? (
        !error && <Notice tone="info">Loading</Notice>
      ) : page.items.length === 0 ? (
        <EmptyState icon={Wrench} title={shown === "open" ? "No work order is open." : "No work orders found."} />
      ) : (
        <>
          <Wide>
            <table className="w-full text-base">
              <thead>
                <tr>
                  <th className="text-left">#</th>
                  <th className="text-left">Property / Unit</th>
                  <th className="text-left">Title</th>
                  <th className="text-left">Vendor</th>
                  <th className="text-left">Priority</th>
                  <th className="text-left">Status</th>
                  <th className="text-left">Due</th>
                  <th className="text-right">Days open</th>
                  <th className="text-left">Updated</th>
                </tr>
              </thead>
              <tbody>
                {page.items.map((row) => (
                  <tr key={row.id} aria-label={row.summary} className="cursor-pointer hover:bg-raised"
                    onClick={() => navigate(`/work-orders/${row.id}`)}>
                    <td className="text-ink-muted">{row.number}</td>
                    <td className="text-ink-muted">{whereLabel(row)}</td>
                    <td className="font-medium text-ink">{row.summary}</td>
                    <td className="text-ink-muted">{row.vendor}</td>
                    <td><Badge tone={PRIORITY[row.priority].tone}>{PRIORITY[row.priority].word}</Badge></td>
                    <td><Badge tone={STATUS[row.status].tone} data-testid="status">{STATUS[row.status].word}</Badge></td>
                    <td className={`whitespace-nowrap ${row.overdue ? "text-danger" : ""}`}>{usDate(row.due_on)}</td>
                    <td className="text-right">{row.days_open}</td>
                    <td className="whitespace-nowrap">{usDate(row.updated_at)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </Wide>
          <Pager page={page} onPage={setNumber} />
        </>
      )}
      {opening && (
        <SidePanel title="New work order" onClose={() => setOpening(false)}>
          <NewWorkOrderForm opened={(id) => navigate(`/work-orders/${id}`)} />
        </SidePanel>
      )}
      {onCall && (
        <SidePanel title="On call" onClose={() => setOnCall(false)}>
          <OnCallForm />
        </SidePanel>
      )}
      {recurring && (
        <SidePanel title="Recurring work orders" onClose={() => setRecurring(false)}>
          <RecurringPanel opened={() => setNumber(1)} />
        </SidePanel>
      )}
      <MaintenanceCard />
    </Shell>
  );
}

// Who is on call for emergencies: each one ticked is emailed when a tenant files one, at their
// login's email, while the server sends email. A change is saved at once.
function OnCallForm() {
  const [staff, setStaff] = useState<OnCall[] | null>(null);
  const [error, setError] = useState("");
  const [said, setSaid] = useState("");
  useEffect(() => {
    api.get<OnCall[]>("/api/maintenance/on-call").then(setStaff).catch((failed) => setError(detail(failed)));
  }, []);
  // The box shows the change at once; a refusal puts it back.
  async function change(member: OnCall, on: boolean) {
    setError("");
    setSaid("");
    const before = staff;
    setStaff((now) => now && now.map((m) => (m.staff_id === member.staff_id ? { ...m, on_call: on } : m)));
    try {
      setStaff(await api.post<OnCall[]>("/api/maintenance/on-call", { staff_id: member.staff_id, on_call: on }));
      setSaid(on ? `${member.name} is on call.` : `${member.name} is off call.`);
    } catch (failed) {
      setStaff(before);
      setError(detail(failed));
    }
  }
  if (staff === null) return <Notice tone={error ? "error" : "info"}>{error || "Loading"}</Notice>;
  return (
    <div className="flex flex-col gap-3">
      <p className="text-sm text-ink-muted">
        Whoever is on call is emailed when a tenant marks a request an emergency. Text messages are not sent.
      </p>
      <ul className="flex flex-col gap-2" aria-label="On call">
        {staff.map((member) => (
          <li key={member.staff_id}>
            <label className="flex items-center gap-2 text-base text-ink">
              <input type="checkbox" checked={member.on_call} onChange={(e) => change(member, e.target.checked)} />
              {member.name}
              {!member.emailed && <span className="text-sm text-ink-subtle">(no login to email)</span>}
            </label>
          </li>
        ))}
      </ul>
      <Said>{said}</Said>
      <Notice tone="error">{error}</Notice>
    </div>
  );
}

// Jobs that come back every few months: each listed with its schedule, ended from a day; Add opens
// any already due at once, and the nightly run opens the rest on their days.
function RecurringPanel({ opened }: { opened: () => void }) {
  const [list, setList] = useState<RecurringWorkOrder[] | null>(null);
  const [books, setBooks] = useState<Portfolio | null>(null);
  const [runs, setRuns] = useState(0);
  const [unread, setUnread] = useState("");
  const { said, error, record } = useRecord(() => setRuns((n) => n + 1));
  useEffect(() => {
    api.get<RecurringWorkOrder[]>("/api/work-orders/recurring").then(setList).catch((failed) => setUnread(detail(failed)));
  }, [runs]);
  useEffect(() => {
    api.get<Portfolio>("/api/setup").then(setBooks).catch((failed) => setUnread(detail(failed)));
  }, []);
  if (list === null || books === null) return <Notice tone={unread ? "error" : "info"}>{unread || "Loading"}</Notice>;
  return (
    <div className="flex flex-col gap-4">
      {list.length === 0 ? (
        <p className="text-base text-ink-muted">No work order comes back by itself yet.</p>
      ) : (
        <ul className="flex flex-col divide-y divide-line" aria-label="Recurring work orders">
          {list.map((r) => <RecurringRow key={r.id} item={r} record={record} />)}
        </ul>
      )}
      <Said>{said}</Said>
      <Notice tone="error">{error}</Notice>
      <h3 className="text-sm font-medium uppercase tracking-wide text-ink-muted">Add one</h3>
      <NewRecurringForm books={books} record={record} opened={opened} />
    </div>
  );
}

function RecurringRow({ item, record }: { item: RecurringWorkOrder; record: Recorder }) {
  const [ends, setEnds] = useState(day(new Date()));
  return (
    <li className="flex flex-col gap-2 py-3" aria-label={item.summary}>
      <p className="text-base text-ink">{recurringLine(item)}</p>
      <p className="text-sm text-ink-subtle">
        {PRIORITY[item.priority].word} priority{item.vendor ? `, ${item.vendor}` : ", no vendor"}.{" "}
        {item.last_opened_on ? `Last opened ${usDate(item.last_opened_on)}.` : "None opened yet."}
        {item.ends_on && ` Ends ${usDate(item.ends_on)}.`}
      </p>
      {item.ends_on === null && (
        <Form className="flex flex-wrap items-end gap-2" onSubmit={(event) => {
          event.preventDefault();
          return record(`/api/work-orders/recurring/${item.id}/end`, { ends_on: ends },
            () => `"${item.summary}" stops from ${usDate(ends)}.`);
        }}>
          <Field label={`Stop "${item.summary}" from`} id={`end-${item.id}`} type="date" required value={ends}
            onChange={(e) => setEnds(e.target.value)} />
          <Button type="submit" variant="secondary">Stop it</Button>
        </Form>
      )}
    </li>
  );
}

function NewRecurringForm({ books, record, opened }: { books: Portfolio; record: Recorder; opened: () => void }) {
  const [property, setProperty] = useState(books.properties[0]?.id ?? "");
  const [unit, setUnit] = useState("");
  const [summary, setSummary] = useState("");
  const [months, setMonths] = useState("3");
  const [dayOfMonth, setDayOfMonth] = useState("1");
  const [starts, setStarts] = useState(day(new Date()));
  const [priority, setPriority] = useState<Priority>("normal");
  const [vendor, setVendor] = useState("");
  if (books.properties.length === 0) return <Notice tone="info">Add a property from Properties first.</Notice>;
  const units = books.properties.find((p) => p.id === property)?.units ?? [];
  return (
    <Form className="flex flex-col gap-3" onSubmit={(event) => {
      event.preventDefault();
      const body = {
        property_id: property, unit_id: unit || null, summary, every_months: Number(months),
        day_of_month: Number(dayOfMonth), starts_on: starts, priority, vendor_id: vendor || null,
      };
      return record<{ id: string; opened: number }>("/api/work-orders/recurring", body, (made) => {
        opened();
        const now = made.opened === 0 ? "" : ` ${made.opened} already due opened now.`;
        return `"${summary.trim()}" comes back by itself.${now}`;
      });
    }}>
      <Select label="Property" id="recurring-property" value={property}
        onChange={(event) => { setProperty(event.target.value); setUnit(""); }}>
        {books.properties.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
      </Select>
      <Select label="Unit" id="recurring-unit" value={unit} onChange={(event) => setUnit(event.target.value)}>
        <option value="">The whole property</option>
        {units.map((u) => <option key={u.id} value={u.id}>{u.name}</option>)}
      </Select>
      <Field label="What it is" id="recurring-summary" required maxLength={500} value={summary}
        onChange={(event) => setSummary(event.target.value)} />
      <div className="grid grid-cols-2 gap-3">
        <Select label="Every" id="recurring-months" value={months} onChange={(event) => setMonths(event.target.value)}>
          {Array.from({ length: 12 }, (_, n) => n + 1).map((n) => (
            <option key={n} value={n}>{n === 1 ? "month" : `${n} months`}</option>
          ))}
        </Select>
        <Field label="On day (1 to 28)" id="recurring-day" type="number" min={1} max={28} required value={dayOfMonth}
          onChange={(event) => setDayOfMonth(event.target.value)} />
      </div>
      <div className="grid grid-cols-2 gap-3">
        <Field label="From" id="recurring-starts" type="date" required value={starts}
          onChange={(event) => setStarts(event.target.value)} />
        <Select label="Priority" id="recurring-priority" value={priority}
          onChange={(event) => setPriority(event.target.value as Priority)}>
          {(Object.keys(PRIORITY) as Priority[]).map((p) => <option key={p} value={p}>{PRIORITY[p].word}</option>)}
        </Select>
      </div>
      <Select label="Vendor (optional)" id="recurring-vendor" value={vendor} onChange={(event) => setVendor(event.target.value)}>
        <option value="">None: staff assign each one</option>
        {books.vendors.map((v) => <option key={v.id} value={v.id}>{v.name}</option>)}
      </Select>
      <div><Button type="submit">Add recurring work order</Button></div>
    </Form>
  );
}

// What a work order's details are, as a form fills them in.
type DetailsDraft = { priority: Priority; description: string; dueOn: string; entry: Entry | "" };

function detailsBody(draft: DetailsDraft) {
  return {
    priority: draft.priority,
    description: draft.description.trim() || null,
    due_on: draft.dueOn || null,
    entry: draft.entry || null,
  };
}

function DetailsFields({ draft, change }: { draft: DetailsDraft; change: (part: Partial<DetailsDraft>) => void }) {
  return (
    <>
      <div className="grid grid-cols-2 gap-3">
        <Select label="Priority" id="work-order-priority" value={draft.priority}
          onChange={(e) => change({ priority: e.target.value as Priority })}>
          {(Object.keys(PRIORITY) as Priority[]).map((p) => <option key={p} value={p}>{PRIORITY[p].word}</option>)}
        </Select>
        <Field label="Due (optional)" id="work-order-due" type="date" value={draft.dueOn}
          onChange={(e) => change({ dueOn: e.target.value })} />
      </div>
      <label htmlFor="work-order-description" className="flex flex-col gap-1.5 text-sm text-ink-muted">
        What is wrong (optional)
        <textarea id="work-order-description" rows={4} maxLength={5000} value={draft.description}
          onChange={(e) => change({ description: e.target.value })} className={TEXTAREA} />
      </label>
      <Select label="Entry" id="work-order-entry" value={draft.entry} onChange={(e) => change({ entry: e.target.value as Entry | "" })}>
        <option value="">Not asked</option>
        {(Object.keys(ENTRY) as Entry[]).map((e) => <option key={e} value={e}>{ENTRY[e]}</option>)}
      </Select>
    </>
  );
}

const NO_DETAILS: DetailsDraft = { priority: "normal", description: "", dueOn: "", entry: "" };

function NewWorkOrderForm({ opened }: { opened: (id: string) => void }) {
  const [books, setBooks] = useState<Portfolio | null>(null);
  const [property, setProperty] = useState("");
  const [unit, setUnit] = useState("");
  const [summary, setSummary] = useState("");
  const [draft, setDraft] = useState<DetailsDraft>(NO_DETAILS);
  const [unread, setUnread] = useState("");
  const { error, record } = useRecord(() => undefined);
  useEffect(() => {
    api
      .get<Portfolio>("/api/setup")
      .then((found) => {
        setBooks(found);
        setProperty(found.properties[0]?.id ?? "");
      })
      .catch((failed) => setUnread(detail(failed)));
  }, []);
  if (books === null) return <Notice tone={unread ? "error" : "info"}>{unread || "Loading"}</Notice>;
  if (books.properties.length === 0) return <Notice tone="info">Add a property from Properties first.</Notice>;
  const units = books.properties.find((p) => p.id === property)?.units ?? [];
  return (
    <Form
      className="flex flex-col gap-3"
      onSubmit={(event) => {
        event.preventDefault();
        const body = { property_id: property, summary, ...(unit ? { unit_id: unit } : {}), ...detailsBody(draft) };
        return record<{ id: string }>("/api/work-orders", body, (made) => {
          opened(made.id);
          return `Work order "${summary.trim()}" opened.`;
        });
      }}
    >
      <Select label="Property" id="work-order-property" value={property}
        onChange={(event) => { setProperty(event.target.value); setUnit(""); }}>
        {books.properties.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
      </Select>
      <Select label="Unit" id="work-order-unit" value={unit} onChange={(event) => setUnit(event.target.value)}>
        <option value="">The whole property</option>
        {units.map((u) => <option key={u.id} value={u.id}>{u.name}</option>)}
      </Select>
      <Field label="Title" id="work-order-summary" required maxLength={500} value={summary}
        onChange={(event) => setSummary(event.target.value)} />
      <DetailsFields draft={draft} change={(part) => setDraft({ ...draft, ...part })} />
      <div><Button type="submit">Open work order</Button></div>
      <Notice tone="error">{error}</Notice>
    </Form>
  );
}

type Panel = "details" | "step" | "bill" | "charge";
const TITLES: Record<Panel, string> = {
  details: "Edit details", step: "Next step", bill: "Create bill", charge: "Charge the tenant",
};

// A work order's own page (ADR 0006): what is wrong, its priority, due day and entry permission,
// the tenants of its unit to call, its photos, its steps in order, the bills that name it and the
// request it answers, and what tenants were charged for it. Next step assigns a vendor (not one
// whose insurance ran out), schedules the visit, completes or cancels it; Create bill records the
// vendor's bill naming it; Charge the tenant charges a lease there a fee naming it (q072).
export function WorkOrderPage({ me, id }: { me: Me; id: string }) {
  const [order, setOrder] = useState<WorkOrder | null>(null);
  const [vendors, setVendors] = useState<Named[]>([]);
  const [books, setBooks] = useState<Portfolio | null>(null);
  const [error, setError] = useState("");
  const [panel, setPanel] = useState<Panel | null>(null);
  const [runs, setRuns] = useState(0);
  const { said, error: refused, record } = useRecord(() => setRuns((n) => n + 1));

  useEffect(() => {
    api.get<WorkOrder>(`/api/work-orders/${id}`).then(setOrder).catch((failed) => setError(detail(failed)));
  }, [id, runs]);
  useEffect(() => {
    api.get<Named[]>("/api/maintenance/vendors").then(setVendors).catch(() => setVendors([]));
    api.get<Portfolio>("/api/setup").then(setBooks).catch(() => setBooks(null));
  }, []);
  const recordThenClose: Recorder = async (path, body, done) => {
    const recorded = await record(path, body, done);
    if (recorded) setPanel(null);
    return recorded;
  };

  if (order === null) {
    return (
      <Shell me={me} nav={STAFF_NAV}>
        <PageHeader title="Work order" />
        <Notice tone={error ? "error" : "info"}>{error || "Loading"}</Notice>
      </Shell>
    );
  }
  const closed = order.status === "completed" || order.status === "cancelled";
  return (
    <Shell me={me} nav={STAFF_NAV}>
      <PageHeader title={`#${order.number} ${order.summary}`} actions={
        <div className="flex flex-wrap items-center gap-2">
          {!closed && <Button onClick={() => setPanel("step")}>Next step</Button>}
          <Button variant="secondary" onClick={() => setPanel("bill")}>Create bill</Button>
          {order.leases.length > 0 && (
            <Button variant="secondary" onClick={() => setPanel("charge")}>Charge the tenant</Button>
          )}
          <Button variant="secondary" onClick={() => setPanel("details")}>Edit details</Button>
        </div>
      } />
      <p className="flex flex-wrap items-center gap-3 text-base text-ink-muted">
        <GoLink href="/work-orders" className={LINK}>Work orders</GoLink>
        <Badge tone={STATUS[order.status].tone} data-testid="status">{STATUS[order.status].word}</Badge>
        <Badge tone={PRIORITY[order.priority].tone} data-testid="priority">{PRIORITY[order.priority].word}</Badge>
      </p>
      <Said>{said}</Said>
      {panel === null && <Notice tone="error">{refused}</Notice>}
      <Card title="Summary">
        <dl className="grid grid-cols-2 gap-x-6 gap-y-2 text-base md:grid-cols-3" aria-label="Summary">
          <Item label="Property"><GoLink href={`/properties/${order.property.id}`} className={LINK}>{order.property.name}</GoLink></Item>
          <Item label="Unit">
            {order.unit ? <GoLink href={`/units/${order.unit.id}`} className={LINK}>{order.unit.name}</GoLink> : "The whole property"}
          </Item>
          <Item label="Vendor">
            {order.vendor_id ? <GoLink href={`/vendors/${order.vendor_id}`} className={LINK}>{order.vendor}</GoLink> : "None yet"}
          </Item>
          <Item label="Opened">{usDate(order.opened_at)}</Item>
          <Item label="Due"><span className={order.overdue ? "text-danger" : ""}>{order.due_on ? usDate(order.due_on) : "No day set"}</span></Item>
          <Item label="Scheduled for">
            {order.scheduled_for
              ? `${usDate(order.scheduled_for)}${order.visit_window ? `, ${order.visit_window}` : ""}`
              : "Not scheduled"}
          </Item>
          <Item label="Days open">{daysLabel(order.days_open)}</Item>
          {order.recurring && <Item label="Recurring">Opened by itself, {order.recurring}</Item>}
          <Item label="Billed">{money(order.billed)}</Item>
          <Item label="Entry">{order.entry ? ENTRY[order.entry] : "Not asked"}</Item>
        </dl>
        {order.description && <p className="mt-3 text-base whitespace-pre-line" data-testid="description">{order.description}</p>}
      </Card>
      {(order.tenants.length > 0 || order.request) && (
        <Card title="Tenant">
          {order.request && (
            <div className="mb-2 flex flex-col gap-1">
              <p className="flex flex-wrap items-center gap-2 text-base text-ink-muted" data-testid="request">
                {order.request.emergency && <Badge tone="danger">Emergency</Badge>}
                Requested by {order.request.tenant} on {usDate(order.request.submitted_at)}: {order.request.summary}
              </p>
              <p className="text-sm text-ink-subtle">
                {order.request.entry_allowed ? "The tenant said someone may come in while they're out." : "The tenant asked to be called first."}
              </p>
              {order.request.emergency && (
                <p className="text-sm text-ink-subtle" data-testid="emailed">
                  {order.request.emailed.length > 0
                    ? `Emailed to who was on call: ${order.request.emailed.join(", ")}.`
                    : "No one on call was emailed."}
                </p>
              )}
              {order.request.photos.length > 0 && (
                <ul className="flex flex-wrap gap-3 text-base" aria-label="Request photos">
                  {order.request.photos.map((p) => (
                    <li key={p.id}>
                      <a className={LINK} href={`/api/documents/${p.id}/file`} target="_blank" rel="noreferrer">{p.title}</a>
                    </li>
                  ))}
                </ul>
              )}
            </div>
          )}
          <ul className="flex flex-col gap-1 text-base" aria-label="Tenants">
            {order.tenants.map((t) => (
              <li key={t.name}>
                {t.name}
                {t.contact.phone && <>, <a className={LINK} href={`tel:${t.contact.phone}`}>{t.contact.phone}</a></>}
                {t.contact.email && <>, <a className={LINK} href={`mailto:${t.contact.email}`}>{t.contact.email}</a></>}
              </li>
            ))}
          </ul>
        </Card>
      )}
      <FilesCard kind="work_order_photo" aboutId={order.id} name={order.summary} />
      <Card title="Timeline">
        <ol className="flex flex-col gap-1 text-base" aria-label="Timeline">
          <li><span className="text-ink-muted">{usDate(order.opened_at)}</span> Opened</li>
          {order.steps.map((s, n) => (
            <li key={n}>
              <span className="text-ink-muted">{usDate(s.taken_at)}</span>{" "}
              {stepLine(s.step, s.vendor, s.scheduled_for, s.visit_window)}
              {s.by_vendor && <span className="text-ink-muted"> (by the vendor)</span>}
              {s.note ? <span className="text-ink-muted">: {s.note}</span> : null}
            </li>
          ))}
        </ol>
      </Card>
      <Card title="Bills">
        {order.bills.length === 0 ? (
          <p className="text-base text-ink-muted">No bill names this work order.</p>
        ) : (
          <Wide>
            <table className="w-full text-base">
              <thead>
                <tr>
                  <th className="text-left">Vendor</th>
                  <th className="text-left">Ref #</th>
                  <th className="text-right">Amount</th>
                  <th className="text-left">Status</th>
                </tr>
              </thead>
              <tbody>
                {order.bills.map((bill) => (
                  <tr key={bill.id} aria-label={bill.reference}>
                    <td>{bill.vendor}</td>
                    <td><GoLink href={`/bills/${bill.id}`} className={LINK}>{bill.reference}</GoLink></td>
                    <td className="text-right">{money(bill.amount)}</td>
                    <td><Badge tone={bill.paid ? "positive" : "caution"}>{bill.paid ? "Paid" : "Unpaid"}</Badge></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </Wide>
        )}
      </Card>
      {order.charges.length > 0 && (
        <Card title="Charged to tenants">
          <Wide>
            <table className="w-full text-base" aria-label="Charged to tenants">
              <thead>
                <tr>
                  <th className="text-left">Unit</th>
                  <th className="text-left">Tenants</th>
                  <th className="text-left">Due</th>
                  <th className="text-left">Memo</th>
                  <th className="text-right">Amount</th>
                  <th className="text-right">Open</th>
                </tr>
              </thead>
              <tbody>
                {order.charges.map((c) => (
                  <tr key={c.id}>
                    <td><GoLink href={`/leases/${c.lease_id}`} className={LINK}>{c.unit}</GoLink></td>
                    <td>{c.tenants}</td>
                    <td className="whitespace-nowrap">{usDate(c.due_on)}</td>
                    <td>{c.memo}</td>
                    <td className="text-right">{money(c.amount)}</td>
                    <td className="text-right">{money(c.open)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </Wide>
        </Card>
      )}
      {panel !== null && (
        <SidePanel title={TITLES[panel]} onClose={() => setPanel(null)}>
          <p className="text-base text-ink">#{order.number} {order.summary}</p>
          {panel === "details" && <DetailsForm order={order} record={recordThenClose} />}
          {panel === "step" && <StepForm order={order} vendors={vendors} record={recordThenClose} />}
          {panel === "bill" && (books ? (
            <RecordBillForm books={books} record={recordThenClose}
              start={{ vendorId: order.vendor_id, propertyId: order.property.id, workOrderId: order.id, memo: order.summary }} />
          ) : <Notice tone="info">Loading</Notice>)}
          {panel === "charge" && <ChargeForm order={order} record={recordThenClose} />}
          <Notice tone="error">{refused}</Notice>
        </SidePanel>
      )}
    </Shell>
  );
}

// A fee the tenant owes for the repair, on a lease at the work order's unit (or property). The
// owner still pays the vendor's bill; what the tenant pays comes back to the owner's account.
function ChargeForm({ order, record }: { order: WorkOrder; record: Recorder }) {
  const [lease, setLease] = useState(order.leases[0]?.id ?? "");
  const [amount, setAmount] = useState("");
  const [due, setDue] = useState(day(new Date()));
  const [memo, setMemo] = useState(`Work order #${order.number}: ${order.summary}`.slice(0, 200));
  const chosen = order.leases.find((l) => l.id === lease);
  return (
    <Form className="flex flex-col gap-3" onSubmit={(event) => {
      event.preventDefault();
      const body = { kind: "fee", amount, due_on: due, memo, work_order_id: order.id };
      return record(`/api/rent/leases/${lease}/charges`, body,
        () => `${money(amount)} charged to ${chosen?.tenants ?? "the tenant"}.`);
    }}>
      <Select label="Lease" id="charge-lease" value={lease} onChange={(event) => setLease(event.target.value)}>
        {order.leases.map((l) => <option key={l.id} value={l.id}>{l.unit}: {l.tenants}</option>)}
      </Select>
      <Field label="Amount" id="charge-amount" inputMode="decimal" required value={amount}
        onChange={(event) => setAmount(event.target.value)} />
      <Field label="Due" id="charge-due" type="date" required value={due} onChange={(event) => setDue(event.target.value)} />
      <Field label="Memo" id="charge-memo" required maxLength={200} value={memo} onChange={(event) => setMemo(event.target.value)} />
      <div><Button type="submit">Charge</Button></div>
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

// A step as the timeline reads it: "Assigned to Vendor 1", "Scheduled for 10/20/2026, 8-10 am".
export function stepLine(
  step: WorkOrder["steps"][number]["step"], vendor: string | null, scheduledFor: string | null, slot: string | null = null,
): string {
  if (step === "assigned") return `Assigned to ${vendor ?? "a vendor"}`;
  if (step === "scheduled") return `Scheduled for ${usDate(scheduledFor)}${slot ? `, ${slot}` : ""}`;
  return step === "completed" ? "Completed" : "Cancelled";
}

function DetailsForm({ order, record }: { order: WorkOrder; record: Recorder }) {
  const [draft, setDraft] = useState<DetailsDraft>({
    priority: order.priority, description: order.description ?? "", dueOn: order.due_on ?? "", entry: order.entry ?? "",
  });
  return (
    <Form className="flex flex-col gap-3" onSubmit={(event) => {
      event.preventDefault();
      return record<{ changed: boolean }>(`/api/work-orders/${order.id}/details`, detailsBody(draft),
        (answer) => (answer.changed ? "Saved the details." : "Nothing changed."));
    }}>
      <DetailsFields draft={draft} change={(part) => setDraft({ ...draft, ...part })} />
      <div><Button type="submit">Save</Button></div>
    </Form>
  );
}

type NextStep = "assigned" | "scheduled" | "completed" | "cancelled";

function StepForm({ order, vendors, record }: { order: WorkOrder; vendors: Named[]; record: Recorder }) {
  const [step, setStep] = useState<NextStep>(order.vendor_id ? "scheduled" : "assigned");
  const [vendor, setVendor] = useState("");
  const [on, setOn] = useState("");
  const [slot, setSlot] = useState("");
  const [note, setNote] = useState("");
  const chosen = vendor || vendors[0]?.id || "";
  return (
    <Form
      className="flex flex-col gap-3"
      onSubmit={(event) => {
        event.preventDefault();
        const body = {
          step,
          vendor_id: step === "assigned" ? chosen : null,
          scheduled_for: step === "scheduled" ? on : null,
          visit_window: step === "scheduled" ? slot.trim() || null : null,
          note: note.trim() || null,
        };
        const name = vendors.find((v) => v.id === chosen)?.name ?? "the vendor";
        const word = {
          assigned: `Assigned to ${name}.`, scheduled: `Scheduled for ${usDate(on)}.`, completed: "Completed.", cancelled: "Cancelled.",
        }[step];
        return record(`/api/maintenance/work-orders/${order.id}/steps`, body, () => word);
      }}
    >
      <Select label="What happens next" id="step-kind" value={step} onChange={(event) => setStep(event.target.value as NextStep)}>
        <option value="assigned">Assign a vendor</option>
        {order.vendor_id && <option value="scheduled">Schedule the visit</option>}
        <option value="completed">Completed</option>
        <option value="cancelled">Cancelled</option>
      </Select>
      {step === "assigned" && (
        <Select label="Vendor" id="step-vendor" value={chosen} onChange={(event) => setVendor(event.target.value)}>
          {vendors.map((v) => <option key={v.id} value={v.id}>{v.name}</option>)}
        </Select>
      )}
      {step === "scheduled" && (
        <>
          <Field label="Visit on" id="step-on" type="date" required value={on} onChange={(event) => setOn(event.target.value)} />
          <Field label="Window (optional, like 8-10 am)" id="step-window" maxLength={60} value={slot}
            onChange={(event) => setSlot(event.target.value)} />
        </>
      )}
      <Field label="Note (optional)" id="step-note" maxLength={2000} value={note} onChange={(event) => setNote(event.target.value)} />
      <div><Button type="submit">Record</Button></div>
    </Form>
  );
}
