import { Wrench } from "lucide-react";
import { useEffect, useState } from "react";
import type { Me } from "../api";
import { ApiError, api } from "../api";
import { Badge } from "../components/Badge";
import { Button } from "../components/Button";
import { EmptyState } from "../components/EmptyState";
import { Field } from "../components/Field";
import { Notice } from "../components/Notice";
import { Pager } from "../components/Pager";
import { Select } from "../components/Select";
import { Card, PageHeader, Shell } from "../components/Shell";
import { SidePanel } from "../components/SidePanel";
import { Tabs } from "../components/Tabs";
import { Wide } from "../components/Wide";
import { money, usDate } from "../money";
import type { Paged } from "../rentals";
import type { Named, Portfolio } from "../setup";
import type { WorkOrder, WorkOrderRow } from "../workOrders";
import { STATUS, whereLabel } from "../workOrders";
import { MaintenanceCard } from "./MaintenanceCard";
import { GoLink } from "./NeedsAttention";
import { STAFF_NAV } from "./staffNav";
import { useRecord } from "./useRecord";

const LINK = "font-medium text-iris hover:text-iris-hover hover:underline";

function detail(failed: unknown): string {
  return failed instanceof ApiError ? failed.detail : String(failed);
}

type Show = "open" | "closed" | "all";
const TABS: { value: Show; label: string }[] = [
  { value: "open", label: "Open" },
  { value: "closed", label: "Closed" },
  { value: "all", label: "All" },
];

// Maintenance → Work orders (ADR 0005): every work order a page at a time, open ones first
// shown, each opening its own page; New work order opens one for a property or unit; tenants'
// requests below.
export function WorkOrdersPage({ me }: { me: Me }) {
  const [show, setShow] = useState<Show>("open");
  const [q, setQ] = useState("");
  const [number, setNumber] = useState(1);
  const [page, setPage] = useState<Paged<WorkOrderRow> | null>(null);
  const [error, setError] = useState("");
  const [opening, setOpening] = useState(false);
  const [runs, setRuns] = useState(0);

  useEffect(() => {
    const query = new URLSearchParams({ show, page: String(number) });
    if (q.trim()) query.set("q", q.trim());
    api
      .get<Paged<WorkOrderRow>>(`/api/work-orders?${query}`)
      .then(setPage)
      .catch((failed) => setError(detail(failed)));
  }, [show, q, number, runs]);

  return (
    <Shell me={me} nav={STAFF_NAV}>
      <PageHeader title="Work orders" actions={<Button onClick={() => setOpening(true)}>New work order</Button>} />
      {opening && (
        <SidePanel title="New work order" onClose={() => setOpening(false)}>
          <NewWorkOrderForm opened={() => setRuns((n) => n + 1)} />
        </SidePanel>
      )}
      <Card title="Work orders">
        <Tabs label="Work orders" tabs={TABS} value={show} onChange={(value) => { setShow(value); setNumber(1); }} />
        <div className="mb-4">
          <Field label="Find a work order, property, unit or vendor" id="find-work-order" type="search" value={q}
            onChange={(event) => { setQ(event.target.value); setNumber(1); }} />
        </div>
        <Notice tone="error">{error}</Notice>
        {page === null ? (
          !error && <Notice tone="info">Loading</Notice>
        ) : page.items.length === 0 ? (
          <EmptyState icon={Wrench} title={show === "open" ? "No work order is open." : "No work orders found."} />
        ) : (
          <>
            <Wide>
              <table className="w-full text-base">
                <thead>
                  <tr>
                    <th className="text-left">Work order</th>
                    <th className="text-left">Where</th>
                    <th className="text-left">Opened</th>
                    <th className="text-left">Status</th>
                    <th className="text-left">Vendor</th>
                    <th className="text-right">Billed</th>
                  </tr>
                </thead>
                <tbody>
                  {page.items.map((row) => (
                    <tr key={row.id} aria-label={row.summary}>
                      <td><GoLink href={`/work-orders/${row.id}`} className={LINK}>{row.summary}</GoLink></td>
                      <td className="text-ink-muted">{whereLabel(row)}</td>
                      <td className="whitespace-nowrap">{usDate(row.opened_at)}</td>
                      <td><Badge tone={STATUS[row.status].tone}>{STATUS[row.status].word}</Badge></td>
                      <td className="text-ink-muted">{row.vendor}</td>
                      <td className="text-right">{money(row.billed)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </Wide>
            <Pager page={page} onPage={setNumber} />
          </>
        )}
      </Card>
      <MaintenanceCard />
    </Shell>
  );
}

function NewWorkOrderForm({ opened }: { opened: () => void }) {
  const [books, setBooks] = useState<Portfolio | null>(null);
  const [property, setProperty] = useState("");
  const [unit, setUnit] = useState("");
  const [summary, setSummary] = useState("");
  const [unread, setUnread] = useState("");
  const { said, error, record } = useRecord(opened);
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
    <form
      className="flex flex-col gap-3"
      onSubmit={async (event) => {
        event.preventDefault();
        const body = { property_id: property, summary, ...(unit ? { unit_id: unit } : {}) };
        if (await record("/api/work-orders", body, () => `Work order "${summary.trim()}" opened.`)) setSummary("");
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
      <Field label="What needs doing" id="work-order-summary" required maxLength={500} value={summary}
        onChange={(event) => setSummary(event.target.value)} />
      <div><Button type="submit">Open work order</Button></div>
      <Notice tone="info">{said}</Notice>
      <Notice tone="error">{error}</Notice>
    </form>
  );
}

// A work order: where it stands, its steps, the bills that name it and the request it answers,
// and recording what happens next.
export function WorkOrderPage({ me, id }: { me: Me; id: string }) {
  const [order, setOrder] = useState<WorkOrder | null>(null);
  const [vendors, setVendors] = useState<Named[]>([]);
  const [error, setError] = useState("");

  function refresh() {
    api.get<WorkOrder>(`/api/work-orders/${id}`).then(setOrder).catch((failed) => setError(detail(failed)));
  }
  useEffect(refresh, [id]);
  useEffect(() => {
    api.get<Named[]>("/api/maintenance/vendors").then(setVendors).catch(() => setVendors([]));
  }, []);

  return (
    <Shell me={me} nav={STAFF_NAV}>
      {order === null ? (
        <Notice tone={error ? "error" : "info"}>{error || "Loading"}</Notice>
      ) : (
        <>
          <PageHeader title={order.summary}
            actions={<Badge tone={STATUS[order.status].tone}>{STATUS[order.status].word}</Badge>} />
          <p className="flex flex-wrap gap-x-4 gap-y-1 text-sm text-ink-muted">
            <GoLink href={`/properties/${order.property.id}`} className={LINK}>{order.property.name}</GoLink>
            {order.unit && <GoLink href={`/units/${order.unit.id}`} className={LINK}>{order.unit.name}</GoLink>}
            <span>Opened {usDate(order.opened_at)}</span>
            {order.vendor && <span>Vendor: {order.vendor}</span>}
            <span>Billed {money(order.billed)}</span>
          </p>
          {order.request && (
            <p className="text-sm text-ink-muted" data-testid="request">
              Requested by {order.request.tenant} on {usDate(order.request.submitted_at)}: {order.request.summary}
            </p>
          )}
          <Card title="Steps">
            {order.steps.length === 0 ? (
              <p className="text-sm text-ink-subtle">No step yet.</p>
            ) : (
              <ol className="flex flex-col gap-1 text-base" aria-label="Steps">
                {order.steps.map((s, n) => (
                  <li key={n}>
                    <span className="font-medium first-letter:uppercase">{s.step}</span>
                    <span className="text-ink-muted">
                      {" "}{usDate(s.taken_at)}{s.vendor ? `, to ${s.vendor}` : ""}{s.note ? `: ${s.note}` : ""}
                    </span>
                  </li>
                ))}
              </ol>
            )}
            {(order.status === "open" || order.status === "assigned") && (
              <StepForm id={id} vendors={vendors} refresh={refresh} />
            )}
          </Card>
          <Card title="Bills">
            {order.bills.length === 0 ? (
              <p className="text-sm text-ink-subtle">No bill names this work order.</p>
            ) : (
              <Wide>
                <table className="w-full text-base">
                  <thead>
                    <tr>
                      <th className="text-left">Vendor</th>
                      <th className="text-left">Reference</th>
                      <th className="text-right">Amount</th>
                      <th className="text-left">State</th>
                    </tr>
                  </thead>
                  <tbody>
                    {order.bills.map((bill) => (
                      <tr key={bill.id} aria-label={bill.reference}>
                        <td>{bill.vendor}</td>
                        <td>{bill.reference}</td>
                        <td className="text-right">{money(bill.amount)}</td>
                        <td><Badge tone={bill.paid ? "positive" : "caution"}>{bill.paid ? "Paid" : "Unpaid"}</Badge></td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </Wide>
            )}
          </Card>
        </>
      )}
    </Shell>
  );
}

function StepForm({ id, vendors, refresh }: { id: string; vendors: Named[]; refresh: () => void }) {
  const { said, error, record } = useRecord(refresh);
  const [step, setStep] = useState<"assigned" | "completed" | "cancelled">("assigned");
  const [vendor, setVendor] = useState("");
  const [note, setNote] = useState("");
  const chosen = vendor || vendors[0]?.id || "";
  return (
    <form
      className="mt-4 flex flex-wrap items-end gap-3"
      onSubmit={async (event) => {
        event.preventDefault();
        const body = { step, vendor_id: step === "assigned" ? chosen : null, note: note.trim() || null };
        const word = { assigned: "Assigned", completed: "Completed", cancelled: "Cancelled" }[step];
        if (await record(`/api/maintenance/work-orders/${id}/steps`, body, () => `${word}.`)) setNote("");
      }}
    >
      <Select label="What happened" id="step-kind" value={step}
        onChange={(event) => setStep(event.target.value as typeof step)}>
        <option value="assigned">Assigned to a vendor</option>
        <option value="completed">Completed</option>
        <option value="cancelled">Cancelled</option>
      </Select>
      {step === "assigned" && (
        <Select label="Vendor" id="step-vendor" value={chosen} onChange={(event) => setVendor(event.target.value)}>
          {vendors.map((v) => <option key={v.id} value={v.id}>{v.name}</option>)}
        </Select>
      )}
      <Field label="Note (optional)" id="step-note" maxLength={2000} value={note}
        onChange={(event) => setNote(event.target.value)} />
      <Button type="submit">Record</Button>
      <Notice tone="info">{said}</Notice>
      <Notice tone="error">{error}</Notice>
    </form>
  );
}
