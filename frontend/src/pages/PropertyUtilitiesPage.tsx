import { useEffect, useState } from "react";
import type { Me } from "../api";
import { ApiError, api } from "../api";
import { Button } from "../components/Button";
import { Field } from "../components/Field";
import { Form } from "../components/Form";
import { Notice } from "../components/Notice";
import { RowMenu } from "../components/RowMenu";
import { Said } from "../components/Said";
import { Select } from "../components/Select";
import { Card, PageHeader, Shell } from "../components/Shell";
import { SidePanel } from "../components/SidePanel";
import { Wide } from "../components/Wide";
import type { Keep } from "../latest";
import { keepAll, latest } from "../latest";
import { day, money, usDate } from "../money";
import type { BilledLease, Method, Plan, Preview, PropertyUtilities, UnitArea, Utility } from "../utilities";
import { METHODS, UTILITIES, methodLabel, plansInForce, shareAmount, utilityLabel, weightLabel } from "../utilities";
import { GoLink } from "./NeedsAttention";
import { STAFF_NAV } from "./staffNav";
import type { Recorder } from "./useRecord";
import { useRecord } from "./useRecord";

type Panel =
  | { kind: "plan" }
  | { kind: "area"; unit: UnitArea }
  | { kind: "billed"; lease: BilledLease }
  | { kind: "bill" };
const TITLES = { plan: "Set plan", area: "Square feet", billed: "Utilities billed", bill: "Bill back a utility" };

const LINK = "font-medium text-iris hover:text-iris-hover hover:underline";

function detail(failed: unknown): string {
  return failed instanceof ApiError ? failed.detail : String(failed);
}

// A property's Utilities page (RUBS and submeters; migration 0096, the founder's review q021; ADR
// 0010 phase 3, part N: the only place a utility is billed back, opened from the property's page):
// its plan for each utility, its units' square feet, the utilities each lease is billed for, and
// its bills, each shared among the leases. Bill back a utility previews each lease's share before
// anything is charged. Tenants are never charged more than the bill less the owner's share; a
// vacant unit's days and a lease not billed stay with the owner.
export function PropertyUtilitiesPage({ me, id }: { me: Me; id: string }) {
  const property = id;
  const [page, setPage] = useState<PropertyUtilities | null>(null);
  const [error, setError] = useState("");
  const [panel, setPanel] = useState<Panel | null>(null);
  const { said, error: refused, record } = useRecord(refresh);

  function refresh(keep: Keep = keepAll) {
    api.get<PropertyUtilities>(`/api/utilities/properties/${property}`).then(keep(setPage)).catch(keep((failed) => setError(detail(failed))));
  }
  useEffect(() => {
    const run = latest();
    refresh(run.keep);
    return run.stop;
  }, [property]);

  const recordThenClose: Recorder = async (path, body, done) => {
    const recorded = await record(path, body, done);
    if (recorded) setPanel(null);
    return recorded;
  };
  const today = day(new Date());

  return (
    <Shell me={me} nav={STAFF_NAV}>
      <PageHeader title={page ? `Utilities · ${page.property}` : "Utilities"} actions={
        page && <Button onClick={() => setPanel({ kind: "bill" })}>Bill back a utility</Button>
      } />
      {page && (
        <p className="text-sm text-ink-subtle">
          <GoLink href={`/properties/${property}`} className={LINK}>{page.property}</GoLink>: its utility plans,
          what each lease is billed for, and each bill shared among the leases.
        </p>
      )}
      <Notice tone="error">{error}</Notice>
      <Said>{said}</Said>
      {page === null ? (
        !error && <Notice tone="info">Loading</Notice>
      ) : (
        <>
          {page && (
            <div className="flex flex-col gap-6">
              <Card title="Plans">
                <div className="flex flex-col gap-3">
                  {page.plans.length === 0 ? (
                    <p className="text-sm text-ink-subtle">No utility is billed back yet.</p>
                  ) : (
                    <Wide>
                      <table className="w-full text-base" aria-label="Plans">
                        <thead>
                          <tr>
                            <th className="text-left">Utility</th>
                            <th className="text-left">From</th>
                            <th className="text-left">How it's shared</th>
                            <th className="text-right">Owner keeps</th>
                            <th className="text-left">Recorded by</th>
                          </tr>
                        </thead>
                        <tbody>
                          {page.plans.map((p: Plan) => (
                            <tr key={p.id} aria-label={`${utilityLabel(p.utility)} from ${usDate(p.starts_on)}`}>
                              <td>{utilityLabel(p.utility)}</td>
                              <td>{usDate(p.starts_on)}</td>
                              <td>{methodLabel(p.method)}</td>
                              <td className="text-right">{p.method === "none" ? "" : `${p.owner_percent}%`}</td>
                              <td className="text-ink-muted">{p.recorded_by ?? ""}</td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </Wide>
                  )}
                  <div><Button variant="secondary" onClick={() => setPanel({ kind: "plan" })}>Set plan</Button></div>
                </div>
              </Card>
              <Card title="Leases">
                {page.leases.length === 0 ? (
                  <p className="text-sm text-ink-subtle">No lease in effect.</p>
                ) : (
                  <Wide>
                    <table className="w-full text-base" aria-label="Leases">
                      <thead>
                        <tr>
                          <th className="text-left">Lease</th>
                          <th className="text-left">Billed for</th>
                          <th><span className="sr-only">Actions</span></th>
                        </tr>
                      </thead>
                      <tbody>
                        {page.leases.map((lease) => (
                          <tr key={lease.lease_id} aria-label={lease.home}>
                            <td>{lease.home}</td>
                            <td className="text-ink-muted">{lease.billed.map(utilityLabel).join(", ") || "Nothing"}</td>
                            <td className="text-right">
                              <RowMenu label={lease.home} actions={[
                                { label: "Utilities billed", onSelect: () => setPanel({ kind: "billed", lease }) },
                              ]} />
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </Wide>
                )}
              </Card>
              <Card title="Units">
                <Wide>
                  <table className="w-full text-base" aria-label="Units">
                    <thead>
                      <tr>
                        <th className="text-left">Unit</th>
                        <th className="text-right">Square feet</th>
                        <th><span className="sr-only">Actions</span></th>
                      </tr>
                    </thead>
                    <tbody>
                      {page.units.map((unit) => (
                        <tr key={unit.unit_id} aria-label={unit.unit}>
                          <td>{unit.unit}</td>
                          <td className="text-right">{unit.square_feet === null ? "Not on file" : unit.square_feet.toLocaleString("en-US")}</td>
                          <td className="text-right">
                            <RowMenu label={unit.unit} actions={[
                              { label: "Set square feet", onSelect: () => setPanel({ kind: "area", unit }) },
                            ]} />
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </Wide>
              </Card>
              <Card title="Bills">
                {page.bills.length === 0 ? (
                  <p className="text-sm text-ink-subtle">No bill billed back yet.</p>
                ) : (
                  <ul className="flex flex-col gap-4" aria-label="Bills">
                    {page.bills.map((bill) => (
                      <li key={bill.id} aria-label={`${utilityLabel(bill.utility)}, ${usDate(bill.period_start)} to ${usDate(bill.period_end)}`}>
                        <p className="text-base text-ink">
                          {`${utilityLabel(bill.utility)}, ${usDate(bill.period_start)} to ${usDate(bill.period_end)}: ${money(bill.amount)}, ${methodLabel(bill.method).toLowerCase()}`}
                          {bill.memo ? ` (${bill.memo})` : ""}
                        </p>
                        <p className="text-sm text-ink-muted" data-testid="bill-totals">
                          {`Charged ${money(bill.charged)}, due ${usDate(bill.due_on)}; the owner keeps ${money(bill.owner_keeps)}.`}
                        </p>
                        <SharesTable method={bill.method} shares={bill.shares} />
                      </li>
                    ))}
                  </ul>
                )}
              </Card>
            </div>
          )}
        </>
      )}
      {panel !== null && page && (
        <SidePanel title={TITLES[panel.kind]} onClose={() => setPanel(null)}>
          {panel.kind === "plan" && <PlanForm property={property} record={recordThenClose} />}
          {panel.kind === "area" && <AreaForm unit={panel.unit} record={recordThenClose} />}
          {panel.kind === "billed" && <BilledForm lease={panel.lease} today={today} record={recordThenClose} />}
          {panel.kind === "bill" && (
            <BillForm property={property} page={page} inForce={plansInForce(page.plans, today)} record={recordThenClose} />
          )}
          <Notice tone="error">{refused}</Notice>
        </SidePanel>
      )}
    </Shell>
  );
}

function SharesTable({ method, shares }: { method: Method; shares: Preview["shares"] }) {
  if (shares.length === 0) return <p className="text-sm text-ink-subtle">No lease in effect in the period.</p>;
  return (
    <Wide>
      <table className="w-full text-base" aria-label="Shares">
        <thead>
          <tr>
            <th className="text-left">Lease</th>
            <th className="text-left">Worked out from</th>
            <th className="text-right">Charged</th>
          </tr>
        </thead>
        <tbody>
          {shares.map((share) => (
            <tr key={share.lease_id} aria-label={share.home}>
              <td>{share.home}</td>
              <td className="text-ink-muted">{weightLabel(method, share)}</td>
              <td className="text-right">{shareAmount(share)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </Wide>
  );
}

function UtilitySelect({ id, value, onChange }: { id: string; value: Utility; onChange: (u: Utility) => void }) {
  return (
    <Select label="Utility" id={id} value={value} onChange={(e) => onChange(e.target.value as Utility)}>
      {UTILITIES.map((u) => <option key={u.value} value={u.value}>{u.label}</option>)}
    </Select>
  );
}

function PlanForm({ property, record }: { property: string; record: Recorder }) {
  const [utility, setUtility] = useState<Utility>("water");
  const [method, setMethod] = useState<Method | "none">("units");
  const [startsOn, setStartsOn] = useState(day(new Date()));
  const [ownerPercent, setOwnerPercent] = useState("0");
  return (
    <Form className="flex flex-col gap-3" onSubmit={(event) => {
      event.preventDefault();
      const body = { utility, method, starts_on: startsOn, owner_percent: ownerPercent || "0" };
      return record(`/api/utilities/properties/${property}/plans`, body, () =>
        method === "none"
          ? `${utilityLabel(utility)} is no longer billed back from ${usDate(startsOn)}.`
          : `${utilityLabel(utility)} is billed back ${methodLabel(method).toLowerCase()} from ${usDate(startsOn)}.`);
    }}>
      <p className="text-sm text-ink-muted">
        How the property shares a utility's bill among its leases from a day. The owner keeps their share (the common
        areas'), a vacant unit's days and any lease not billed for it; each share is rounded down to the cent.
      </p>
      <UtilitySelect id="plan-utility" value={utility} onChange={setUtility} />
      <Select label="How it's shared" id="plan-method" value={method} onChange={(e) => setMethod(e.target.value as Method | "none")}>
        {METHODS.map((m) => <option key={m.value} value={m.value}>{m.label}</option>)}
      </Select>
      <Field label="From" id="plan-from" type="date" required value={startsOn} onChange={(e) => setStartsOn(e.target.value)} />
      {method !== "none" && (
        <Field label="Owner keeps (percent, for the common areas)" id="plan-owner" inputMode="decimal" value={ownerPercent}
          onChange={(e) => setOwnerPercent(e.target.value)} />
      )}
      <div>
        <Button type="submit" says={method === "none"
          ? "Stops billing it back from that day; the plan before stays in the history. Nothing is charged now."
          : "Shares this utility's bills that way from that day; the plan before stays in the history. Nothing is charged now."}>
          Save plan
        </Button>
      </div>
    </Form>
  );
}

function AreaForm({ unit, record }: { unit: UnitArea; record: Recorder }) {
  const [area, setArea] = useState(unit.square_feet === null ? "" : String(unit.square_feet));
  return (
    <Form className="flex flex-col gap-3" onSubmit={(event) => {
      event.preventDefault();
      return record(`/api/utilities/units/${unit.unit_id}/area`, { square_feet: Number(area) },
        () => `${unit.unit}: ${Number(area).toLocaleString("en-US")} square feet.`);
    }}>
      <Field label={`Square feet, ${unit.unit}`} id="unit-area" inputMode="numeric" required value={area}
        onChange={(e) => setArea(e.target.value)} />
      <div>
        <Button type="submit" says="Bills shared by square feet use it from now on; bills already shared keep theirs. Nothing is charged.">
          Save
        </Button>
      </div>
    </Form>
  );
}

function BilledForm({ lease, today, record }: { lease: BilledLease; today: string; record: Recorder }) {
  const [utility, setUtility] = useState<Utility>("water");
  const [billed, setBilled] = useState(true);
  const [startsOn, setStartsOn] = useState(lease.starts_on > today ? lease.starts_on : today);
  return (
    <Form className="flex flex-col gap-3" onSubmit={(event) => {
      event.preventDefault();
      return record(`/api/utilities/leases/${lease.lease_id}/billed`, { utility, billed, starts_on: startsOn }, () =>
        `${lease.home} is ${billed ? "" : "not "}billed for ${utilityLabel(utility).toLowerCase()} from ${usDate(startsOn)}.`);
    }}>
      <p className="text-sm text-ink-muted">As the lease says: a tenant is billed only for the utilities their lease names.</p>
      <UtilitySelect id="billed-utility" value={utility} onChange={setUtility} />
      <label className="flex items-center gap-2 text-sm text-ink">
        <input type="checkbox" checked={billed} onChange={(e) => setBilled(e.target.checked)} />
        Billed for it
      </label>
      <Field label="From" id="billed-from" type="date" required value={startsOn} onChange={(e) => setStartsOn(e.target.value)} />
      <div>
        <Button type="submit" says="Bills from that day follow it; the choice before stays in the history. Nothing is charged now.">
          Save
        </Button>
      </div>
    </Form>
  );
}

function BillForm({ property, page, inForce, record }: {
  property: string; page: PropertyUtilities; inForce: Plan[]; record: Recorder;
}) {
  const [utility, setUtility] = useState<Utility>(inForce[0]?.utility ?? "water");
  const [start, setStart] = useState("");
  const [end, setEnd] = useState("");
  const [amount, setAmount] = useState("");
  const [dueOn, setDueOn] = useState(day(new Date()));
  const [memo, setMemo] = useState("");
  const [master, setMaster] = useState("");
  const [readings, setReadings] = useState<Record<string, string>>({});
  const [preview, setPreview] = useState<Preview | null>(null);
  // Whose readings to ask for: the leases in effect today, then those of the period previewed.
  const [readers, setReaders] = useState(page.leases.map((l) => ({ lease_id: l.lease_id, home: l.home })));
  const [error, setError] = useState("");
  const metered = inForce.find((p) => p.utility === utility)?.method === "submeter";
  const asked = () => ({
    property_id: property, utility, period_start: start, period_end: end, amount,
    ...(metered ? {
      master_usage: master,
      usage: Object.entries(readings).filter(([, v]) => v.trim() !== "").map(([lease_id, usage]) => ({ lease_id, usage: usage.trim() })),
    } : {}),
  });
  // A change to what is asked makes the preview stale.
  const changed = <T,>(set: (value: T) => void) => (value: T) => { set(value); setPreview(null); };
  async function look() {
    setError("");
    try {
      const shown = await api.post<Preview>("/api/utilities/preview", asked());
      setPreview(shown);
      setReaders(shown.shares.map((s) => ({ lease_id: s.lease_id, home: s.home })));
    } catch (failed) {
      setError(detail(failed));
    }
  }
  return (
    <Form className="flex flex-col gap-3" onSubmit={(event) => {
      event.preventDefault();
      if (!preview) return look();
      return record("/api/utilities/bills", { ...asked(), due_on: dueOn, memo: memo.trim() || null },
        () => `${utilityLabel(utility)} billed back: ${money(preview.charged)} charged to the leases; the owner keeps ${money(preview.owner_keeps)}.`);
    }}>
      <UtilitySelect id="bill-utility" value={utility} onChange={changed(setUtility)} />
      <Field label="Period from" id="bill-start" type="date" required value={start} onChange={(e) => changed(setStart)(e.target.value)} />
      <Field label="Period to" id="bill-end" type="date" required value={end} onChange={(e) => changed(setEnd)(e.target.value)} />
      <Field label="The bill's amount" id="bill-amount" inputMode="decimal" required value={amount}
        onChange={(e) => changed(setAmount)(e.target.value)} />
      {metered && (
        <>
          <Field label="Master meter's reading for the period" id="bill-master" inputMode="decimal" required value={master}
            onChange={(e) => changed(setMaster)(e.target.value)} />
          {readers.map((lease) => (
            <Field key={lease.lease_id} label={`Reading, ${lease.home}`} id={`bill-reading-${lease.lease_id}`} inputMode="decimal"
              value={readings[lease.lease_id] ?? ""}
              onChange={(e) => changed(setReadings)({ ...readings, [lease.lease_id]: e.target.value })} />
          ))}
        </>
      )}
      <Field label="Due on" id="bill-due" type="date" required value={dueOn} onChange={(e) => setDueOn(e.target.value)} />
      <Field label="Memo (optional)" id="bill-memo" maxLength={100} value={memo} onChange={(e) => setMemo(e.target.value)} />
      {preview && (
        <div className="flex flex-col gap-2" data-testid="bill-preview">
          <p className="text-sm text-ink">
            {`${methodLabel(preview.method)}, the owner keeping ${preview.owner_percent}%: ${money(preview.charged)} to the leases, ${money(preview.owner_keeps)} stays with the owner.`}
          </p>
          <SharesTable method={preview.method} shares={preview.shares} />
        </div>
      )}
      <Notice tone="error">{error}</Notice>
      <div className="flex gap-2">
        <Button type="submit" says={preview
          ? `Charges the leases ${money(preview.charged)} in all, due ${usDate(dueOn)}; the owner keeps ${money(preview.owner_keeps)}. No one is emailed.`
          : "Shows each lease's share first. Nothing is charged until you press Charge the leases."}>
          {preview ? "Charge the leases" : "Preview"}
        </Button>
      </div>
    </Form>
  );
}
