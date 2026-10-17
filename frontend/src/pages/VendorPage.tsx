import { useEffect, useState } from "react";
import type { Me } from "../api";
import { ApiError, api } from "../api";
import { Badge } from "../components/Badge";
import { Button } from "../components/Button";
import { ContactCard } from "../components/ContactCard";
import { Field } from "../components/Field";
import { Form } from "../components/Form";
import { Notice } from "../components/Notice";
import { RecordActions } from "../components/RecordActions";
import { Said } from "../components/Said";
import { Select } from "../components/Select";
import { Card, PageHeader, Shell } from "../components/Shell";
import { SidePanel } from "../components/SidePanel";
import { Tabs } from "../components/Tabs";
import { Wide } from "../components/Wide";
import { day, money, usDate } from "../money";
import { COVERAGES, PAYMENT, standingTone, standingWord } from "../vendors";
import type { VendorPageData } from "../workOrders";
import { STATUS } from "../workOrders";
import { EmailsCard } from "../components/EmailsCard";
import { FilesCard } from "./FilesCard";
import { GoLink } from "./NeedsAttention";
import { STAFF_NAV } from "./staffNav";
import type { Recorder } from "./useRecord";
import { useRecord } from "./useRecord";
import type { ProfileDraft } from "./VendorsPage";
import { ProfileFields, profileBody } from "./VendorsPage";

const LINK = "font-medium text-iris hover:text-iris-hover hover:underline";

type Tab = "summary" | "insurance" | "bills" | "work-orders" | "email" | "files";
const TABS: { value: Tab; label: string }[] = [
  { value: "summary", label: "Summary" },
  { value: "insurance", label: "Insurance" },
  { value: "bills", label: "Bills" },
  { value: "work-orders", label: "Work orders" },
  { value: "email", label: "Email" },
  { value: "files", label: "Files" },
];
type Panel = "profile" | "policy";

// A vendor (ADR 0005, ADR 0006): tabs Summary (their trade, whether they get a 1099, how they are
// paid, W-9 on file, contact details), Insurance (their policies and Add policy), Bills, Work
// orders and Files (the W-9 lives there); renamed in place. No tax ID is kept: it stays on the W-9.
export function VendorPage({ me, id }: { me: Me; id: string }) {
  const [vendor, setVendor] = useState<VendorPageData | null>(null);
  const [error, setError] = useState("");
  const [tab, setTab] = useState<Tab>("summary");
  const [panel, setPanel] = useState<Panel | null>(null);
  const [renamed, setRenamed] = useState("");
  const { said, error: refused, record } = useRecord(refresh);

  function refresh() {
    api
      .get<VendorPageData>(`/api/vendors/${id}`)
      .then(setVendor)
      .catch((failed) => setError(failed instanceof ApiError ? failed.detail : String(failed)));
  }
  useEffect(refresh, [id]);
  const recordThenClose: Recorder = async (path, body, done) => {
    const recorded = await record(path, body, done);
    if (recorded) setPanel(null);
    return recorded;
  };

  if (vendor === null) {
    return (
      <Shell me={me} nav={STAFF_NAV}>
        <PageHeader title="Vendor" />
        <Notice tone={error ? "error" : "info"}>{error || "Loading"}</Notice>
      </Shell>
    );
  }
  return (
    <Shell me={me} nav={STAFF_NAV}>
      <PageHeader title={vendor.name} actions={
        <RecordActions path={`/api/vendors/${id}`} name={vendor.name}
          onChange={(done) => { setRenamed(done); refresh(); }} />
      } />
      <p className="flex flex-wrap items-center gap-3 text-sm text-ink-muted">
        <GoLink href="/vendors" className={LINK}>Vendors</GoLink>
        <Badge tone={vendor.w9_on_file ? "positive" : "caution"}>{vendor.w9_on_file ? "W-9 on file" : "W-9 missing"}</Badge>
        <Badge tone={standingTone(vendor.insurance_standing)} data-testid="standing">
          {`Insurance: ${standingWord(vendor.insurance_standing)}`}
        </Badge>
      </p>
      <Said>{said || renamed}</Said>
      {panel === null && <Notice tone="error">{refused}</Notice>}
      <Tabs label="Vendor" tabs={TABS} value={tab} onChange={setTab} />
      {tab === "summary" && (
        <>
          <Card title="Summary">
            <div className="mb-3"><Button variant="secondary" size="sm" onClick={() => setPanel("profile")}>Edit details</Button></div>
            <dl className="grid grid-cols-2 gap-x-6 gap-y-2 text-base md:grid-cols-4" aria-label="Summary">
              <Item label="Trade">{vendor.profile?.trade ?? "Not set"}</Item>
              <Item label="1099 vendor">{vendor.profile ? (vendor.profile.gets_1099 ? "Yes" : "No") : "Not set"}</Item>
              <Item label="Paid by">{vendor.profile?.payment_method ? PAYMENT[vendor.profile.payment_method] : "Not set"}</Item>
              <Item label="W-9">{vendor.w9_on_file ? "On file" : "Missing"}</Item>
            </dl>
          </Card>
          <ContactCard path={`/api/vendors/${id}`} contact={vendor.contact} refresh={refresh} name={vendor.name} />
        </>
      )}
      {tab === "insurance" && (
        <Card title="Policies">
          <div className="mb-3"><Button size="sm" onClick={() => setPanel("policy")}>Add policy</Button></div>
          {vendor.insurance.length === 0 ? (
            <p className="text-base text-ink-muted">No insurance on file.</p>
          ) : (
            <Wide>
              <table className="w-full text-base" aria-label="Policies">
                <thead>
                  <tr>
                    <th className="text-left">Coverage</th>
                    <th className="text-left">Carrier</th>
                    <th className="text-left">Policy #</th>
                    <th className="text-left">Starts</th>
                    <th className="text-left">Expires</th>
                    <th className="text-right">Limit</th>
                    <th className="text-left">Standing</th>
                  </tr>
                </thead>
                <tbody>
                  {vendor.insurance.map((policy) => (
                    <tr key={policy.coverage} aria-label={policy.coverage}>
                      <td className="first-letter:uppercase">{policy.coverage}</td>
                      <td>{policy.carrier}</td>
                      <td>{policy.policy_number}</td>
                      <td>{usDate(policy.starts_on)}</td>
                      <td>{usDate(policy.expires_on)}</td>
                      <td className="text-right">{policy.limit_amount ? money(policy.limit_amount) : ""}</td>
                      <td><Badge tone={standingTone(policy.standing)}>{standingWord(policy.standing)}</Badge></td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </Wide>
          )}
        </Card>
      )}
      {tab === "bills" && (
        <Card title="Bills">
          {vendor.bills.length === 0 ? (
            <p className="text-base text-ink-muted">No bills yet.</p>
          ) : (
            <Wide>
              <table className="w-full text-base" aria-label="Bills">
                <thead>
                  <tr>
                    <th className="text-left">Ref #</th>
                    <th className="text-left">Owner and property</th>
                    <th className="text-left">Bill date</th>
                    <th className="text-right">Amount</th>
                    <th className="text-left">Status</th>
                  </tr>
                </thead>
                <tbody>
                  {vendor.bills.map((bill) => (
                    <tr key={bill.id} aria-label={bill.reference}>
                      <td><GoLink href={`/bills/${bill.id}`} className={LINK}>{bill.reference}</GoLink></td>
                      <td className="text-ink-muted">{bill.owner}, {bill.property}</td>
                      <td>{usDate(bill.bill_date)}</td>
                      <td className="text-right">{money(bill.amount)}</td>
                      <td><Badge tone={bill.paid ? "positive" : "caution"}>{bill.paid ? "Paid" : "Unpaid"}</Badge></td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </Wide>
          )}
        </Card>
      )}
      {tab === "work-orders" && (
        <Card title="Work orders">
          {vendor.work_orders.length === 0 ? (
            <p className="text-base text-ink-muted">No work order is assigned to them.</p>
          ) : (
            <Wide>
              <table className="w-full text-base" aria-label="Work orders">
                <thead>
                  <tr>
                    <th className="text-left">#</th>
                    <th className="text-left">Title</th>
                    <th className="text-left">Property / Unit</th>
                    <th className="text-left">Opened</th>
                    <th className="text-left">Status</th>
                  </tr>
                </thead>
                <tbody>
                  {vendor.work_orders.map((order) => (
                    <tr key={order.id} aria-label={order.summary}>
                      <td className="text-ink-muted">{order.number}</td>
                      <td><GoLink href={`/work-orders/${order.id}`} className={LINK}>{order.summary}</GoLink></td>
                      <td className="text-ink-muted">{order.unit ? `${order.unit}, ${order.property}` : order.property}</td>
                      <td>{usDate(order.opened_at)}</td>
                      <td><Badge tone={STATUS[order.status].tone}>{STATUS[order.status].word}</Badge></td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </Wide>
          )}
        </Card>
      )}
      {tab === "files" && <FilesCard kind="w9" also={["vendor_document"]} aboutId={id} name={vendor.name} />}
      {tab === "email" && <EmailsCard about="vendor" aboutId={id} name={vendor.name} emailOn={me.email_on} />}
      {panel !== null && (
        <SidePanel title={panel === "profile" ? "Edit details" : "Add policy"} onClose={() => setPanel(null)}>
          <p className="text-base text-ink">{vendor.name}</p>
          {panel === "profile" && <ProfileForm vendor={vendor} record={recordThenClose} />}
          {panel === "policy" && <PolicyForm vendor={vendor} record={recordThenClose} />}
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

function ProfileForm({ vendor, record }: { vendor: VendorPageData; record: Recorder }) {
  const [draft, setDraft] = useState<ProfileDraft>({
    trade: vendor.profile?.trade ?? "",
    gets1099: vendor.profile?.gets_1099 ?? false,
    method: vendor.profile?.payment_method ?? "",
  });
  return (
    <Form className="flex flex-col gap-3" onSubmit={(event) => {
      event.preventDefault();
      return record<{ changed: boolean }>(`/api/vendors/${vendor.id}/profile`, profileBody(draft),
        (answer) => (answer.changed ? `Saved ${vendor.name}.` : "Nothing changed."));
    }}>
      <ProfileFields draft={draft} change={(part) => setDraft({ ...draft, ...part })} />
      <div><Button type="submit">Save</Button></div>
    </Form>
  );
}

// A policy of the vendor's, or its renewal (a new policy).
function PolicyForm({ vendor, record }: { vendor: VendorPageData; record: Recorder }) {
  const [coverage, setCoverage] = useState<string>(COVERAGES[0]);
  const [carrier, setCarrier] = useState("");
  const [number, setNumber] = useState("");
  const [starts, setStarts] = useState(day(new Date()));
  const [expires, setExpires] = useState("");
  const [limit, setLimit] = useState("");
  return (
    <Form className="flex flex-col gap-3" onSubmit={(event) => {
      event.preventDefault();
      const body = {
        coverage, carrier, policy_number: number, starts_on: starts, expires_on: expires,
        ...(limit.trim() ? { limit_amount: limit.trim() } : {}),
      };
      return record(`/api/vendors/${vendor.id}/insurance`, body, () => `${vendor.name}'s ${coverage} policy ${number} is recorded.`);
    }}>
      <Select label="Coverage" id="policy-coverage" value={coverage} onChange={(e) => setCoverage(e.target.value)}>
        {COVERAGES.map((c) => <option key={c} value={c}>{c}</option>)}
      </Select>
      <Field label="Carrier" id="policy-carrier" required maxLength={200} value={carrier}
        onChange={(e) => setCarrier(e.target.value)} />
      <Field label="Policy number" id="policy-number" required maxLength={100} value={number}
        onChange={(e) => setNumber(e.target.value)} />
      <div className="grid grid-cols-2 gap-3">
        <Field label="Starts" id="policy-starts" type="date" required value={starts}
          onChange={(e) => setStarts(e.target.value)} />
        <Field label="Expires" id="policy-expires" type="date" required value={expires}
          onChange={(e) => setExpires(e.target.value)} />
      </div>
      <Field label="Limit (optional)" id="policy-limit" inputMode="decimal" value={limit}
        onChange={(e) => setLimit(e.target.value)} />
      <div><Button type="submit">Record policy</Button></div>
    </Form>
  );
}
