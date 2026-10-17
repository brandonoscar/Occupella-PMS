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
import { money, usDate } from "../money";
import type { Paged } from "../rentals";
import { navigate } from "../router";
import { scopeParams, useScope } from "../scope";
import type { Named, Portfolio } from "../setup";
import type { Entry, Priority, WorkOrder, WorkOrderRow, WorkOrderStatus } from "../workOrders";
import { ENTRY, PRIORITY, STATUS, whereLabel } from "../workOrders";
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
// priority, status, due day and when it last changed; the row opens its page. New work order
// opens one; tenants' requests are below.
export function WorkOrdersPage({ me }: { me: Me }) {
  const [shown, setShown] = useState<Shown>("open");
  const [scope, setScope] = useScope();
  const [vendor, setVendor] = useState("");
  const [vendors, setVendors] = useState<Named[]>([]);
  const [number, setNumber] = useState(1);
  const [page, setPage] = useState<Paged<WorkOrderRow> | null>(null);
  const [error, setError] = useState("");
  const [opening, setOpening] = useState(false);

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
      <PageHeader title="Work orders" actions={<Button onClick={() => setOpening(true)}>New work order</Button>} />
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
      <MaintenanceCard />
    </Shell>
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
  if (books.properties.length === 0) return <Notice tone="info">Add a property from Settings, Company setup first.</Notice>;
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

type Panel = "details" | "step" | "bill";
const TITLES: Record<Panel, string> = { details: "Edit details", step: "Next step", bill: "Create bill" };

// A work order's own page (ADR 0006): what is wrong, its priority, due day and entry permission,
// the tenants of its unit to call, its photos, its steps in order, the bills that name it and the
// request it answers. Next step assigns a vendor (not one whose insurance ran out), schedules the
// visit, completes or cancels it; Create bill records the vendor's bill naming it.
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
          <Item label="Scheduled for">{order.scheduled_for ? usDate(order.scheduled_for) : "Not scheduled"}</Item>
          <Item label="Billed">{money(order.billed)}</Item>
          <Item label="Entry">{order.entry ? ENTRY[order.entry] : "Not asked"}</Item>
        </dl>
        {order.description && <p className="mt-3 text-base whitespace-pre-line" data-testid="description">{order.description}</p>}
      </Card>
      {(order.tenants.length > 0 || order.request) && (
        <Card title="Tenant">
          {order.request && (
            <p className="text-base text-ink-muted" data-testid="request">
              Requested by {order.request.tenant} on {usDate(order.request.submitted_at)}: {order.request.summary}
            </p>
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
              {stepLine(s.step, s.vendor, s.scheduled_for)}
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
      {panel !== null && (
        <SidePanel title={TITLES[panel]} onClose={() => setPanel(null)}>
          <p className="text-base text-ink">#{order.number} {order.summary}</p>
          {panel === "details" && <DetailsForm order={order} record={recordThenClose} />}
          {panel === "step" && <StepForm order={order} vendors={vendors} record={recordThenClose} />}
          {panel === "bill" && (books ? (
            <RecordBillForm books={books} record={recordThenClose}
              start={{ vendorId: order.vendor_id, propertyId: order.property.id, workOrderId: order.id, memo: order.summary }} />
          ) : <Notice tone="info">Loading</Notice>)}
          <Notice tone="error">{refused}</Notice>
        </SidePanel>
      )}
    </Shell>
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

// A step as the timeline reads it: "Assigned to Vendor 1", "Scheduled for 10/20/2026".
export function stepLine(step: WorkOrder["steps"][number]["step"], vendor: string | null, scheduledFor: string | null): string {
  if (step === "assigned") return `Assigned to ${vendor ?? "a vendor"}`;
  if (step === "scheduled") return `Scheduled for ${usDate(scheduledFor)}`;
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
        <Field label="Visit on" id="step-on" type="date" required value={on} onChange={(event) => setOn(event.target.value)} />
      )}
      <Field label="Note (optional)" id="step-note" maxLength={2000} value={note} onChange={(event) => setNote(event.target.value)} />
      <div><Button type="submit">Record</Button></div>
    </Form>
  );
}
