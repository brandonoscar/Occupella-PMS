import { useEffect, useState } from "react";
import { ApiError, api } from "../api";
import type { LeaseAssistance } from "../assistance";
import { Badge } from "../components/Badge";
import { Button } from "../components/Button";
import { Field } from "../components/Field";
import { Form } from "../components/Form";
import { Notice } from "../components/Notice";
import { Said } from "../components/Said";
import { Select } from "../components/Select";
import { Card } from "../components/Shell";
import { SidePanel } from "../components/SidePanel";
import { Wide } from "../components/Wide";
import { day, money, usDate } from "../money";
import type { LeaseTaxCredit, TaxCreditLimits, UnitTaxCredit } from "../taxCredit";
import {
  HOUSEHOLDS, INCOME_PERCENTS, bedroomsLabel, designationLine, grossLine, incomeTone, rentTone,
} from "../taxCredit";
import { CertificationForm, CertificationsTable } from "./AssistanceCard";
import type { Recorder } from "./useRecord";
import { useRecord } from "./useRecord";

// Tax credit (LIHTC) compliance (migration 0123, the founder's review q143): a property's area
// income limits, a unit's designation and where a tax credit unit's lease stands. The company
// enters the limits from its area's published table each year; the PMS looks nothing up and
// files nothing with the state agency.

function detail(failed: unknown): string {
  return failed instanceof ApiError ? failed.detail : String(failed);
}

// A record's side panel that closes once its save went through.
function useRecordThenClose(refresh: () => void, close: () => void) {
  const { said, error, record } = useRecord(refresh);
  const recordThenClose: Recorder = async (path, body, done) => {
    const recorded = await record(path, body, done);
    if (recorded) close();
    return recorded;
  };
  return { said, error, record: recordThenClose };
}

// On a property's page: its 50% of median income limits from each day, the latest first.
export function TaxCreditLimitsCard({ propertyId }: { propertyId: string }) {
  const [limits, setLimits] = useState<TaxCreditLimits[] | null>(null);
  const [error, setError] = useState("");
  const [setting, setSetting] = useState(false);
  function refresh() {
    api.get<TaxCreditLimits[]>(`/api/tax-credit/properties/${propertyId}`).then(setLimits)
      .catch((failed) => setError(detail(failed)));
  }
  useEffect(refresh, [propertyId]);
  const { said, error: refused, record } = useRecordThenClose(refresh, () => setSetting(false));

  return (
    <Card title="Tax credit income limits">
      <div className="flex flex-col gap-3">
        <Notice tone="error">{error}</Notice>
        <Said>{said}</Said>
        {limits !== null && limits.length === 0 && (
          <p className="text-sm text-ink-muted" data-testid="no-income-limits">
            No income limits on file. A property with tax credit units takes its area&apos;s limits at 50% of median
            each year, as they are published.
          </p>
        )}
        {limits !== null && limits.length > 0 && (
          <Wide>
            <table className="w-full text-base" aria-label="Income limits at 50% of median">
              <thead>
                <tr>
                  <th className="text-left">From</th>
                  {HOUSEHOLDS.map((h) => <th key={h} className="text-right">{h}</th>)}
                </tr>
              </thead>
              <tbody>
                {limits.map((l) => (
                  <tr key={l.starts_on} aria-label={`From ${usDate(l.starts_on)}`}>
                    <td className="whitespace-nowrap">{usDate(l.starts_on)}</td>
                    {l.limits.map((v, n) => <td key={n} className="text-right">{money(v)}</td>)}
                  </tr>
                ))}
              </tbody>
            </table>
          </Wide>
        )}
        <div><Button variant="secondary" onClick={() => setSetting(true)}>Set income limits</Button></div>
      </div>
      {setting && (
        <SidePanel title="Set income limits" onClose={() => setSetting(false)}>
          <LimitsForm propertyId={propertyId} record={record} />
          <Notice tone="error">{refused}</Notice>
        </SidePanel>
      )}
    </Card>
  );
}

function LimitsForm({ propertyId, record }: { propertyId: string; record: Recorder }) {
  const [from, setFrom] = useState(day(new Date()));
  const [values, setValues] = useState<string[]>(HOUSEHOLDS.map(() => ""));
  return (
    <Form className="flex flex-col gap-3" onSubmit={(event) => {
      event.preventDefault();
      const body = { property_id: propertyId, starts_on: from, limits: values.map((v) => v.trim()) };
      return record("/api/tax-credit/limits", body, () => `Income limits from ${usDate(from)} saved.`);
    }}>
      <p className="text-sm text-ink-muted">
        The area&apos;s income limits at 50% of median, for households of one to eight, as its published table gives
        them. Each other percent of median is in proportion: 60% is 1.2 times these.
      </p>
      <Field label="From" id="limits-from" type="date" required value={from} onChange={(e) => setFrom(e.target.value)} />
      {HOUSEHOLDS.map((household, n) => (
        <Field key={household} label={household} id={`limit-${n + 1}`} inputMode="decimal" required value={values[n]}
          onChange={(e) => setValues(values.map((v, m) => (m === n ? e.target.value : v)))} />
      ))}
      <div><Button type="submit">Save income limits</Button></div>
    </Form>
  );
}

// On a unit's page: its designation in force today, the most its gross rent may be, and every
// designation from a day.
export function TaxCreditUnitCard({ unitId }: { unitId: string }) {
  const [unit, setUnit] = useState<UnitTaxCredit | null>(null);
  const [error, setError] = useState("");
  const [setting, setSetting] = useState(false);
  function refresh() {
    api.get<UnitTaxCredit>(`/api/tax-credit/units/${unitId}`).then(setUnit)
      .catch((failed) => setError(detail(failed)));
  }
  useEffect(refresh, [unitId]);
  const { said, error: refused, record } = useRecordThenClose(refresh, () => setSetting(false));
  const inForce = unit?.in_force ?? null;

  return (
    <Card title="Tax credit">
      <div className="flex flex-col gap-3">
        <Notice tone="error">{error}</Notice>
        <Said>{said}</Said>
        {unit !== null && (
          <p className="text-base" data-testid="tax-credit-unit">
            {inForce === null || inForce.income_percent === null || inForce.bedrooms === null
              ? "Not a tax credit unit."
              : `A tax credit unit at ${inForce.income_percent}% of median, ${bedroomsLabel(inForce.bedrooms)}. ` +
                (unit.max_gross_rent === null
                  ? "Its rent limit waits for the property's income limits."
                  : `The rent and the ${money(inForce.utility_allowance)} utility allowance may come to ${money(unit.max_gross_rent)} a month.`)}
          </p>
        )}
        {unit !== null && unit.designations.length > 0 && (
          <ul className="text-sm text-ink-muted" aria-label="Tax credit designations">
            {unit.designations.map((d) => <li key={d.starts_on}>{designationLine(d)}</li>)}
          </ul>
        )}
        <div><Button variant="secondary" onClick={() => setSetting(true)}>Set tax credit</Button></div>
      </div>
      {setting && (
        <SidePanel title="Set tax credit" onClose={() => setSetting(false)}>
          <DesignationForm unitId={unitId} record={record} />
          <Notice tone="error">{refused}</Notice>
        </SidePanel>
      )}
    </Card>
  );
}

function DesignationForm({ unitId, record }: { unitId: string; record: Recorder }) {
  const [from, setFrom] = useState(day(new Date()));
  const [percent, setPercent] = useState("60");
  const [bedrooms, setBedrooms] = useState("1");
  const [allowance, setAllowance] = useState("");
  const ending = percent === "";
  return (
    <Form className="flex flex-col gap-3" onSubmit={(event) => {
      event.preventDefault();
      const body = ending
        ? { unit_id: unitId, starts_on: from, income_percent: null }
        : {
          unit_id: unitId, starts_on: from, income_percent: Number(percent), bedrooms: Number(bedrooms),
          utility_allowance: allowance.trim(),
        };
      return record("/api/tax-credit/units", body, () =>
        ending ? `Not a tax credit unit from ${usDate(from)}.` : `A tax credit unit at ${percent}% of median from ${usDate(from)}.`);
    }}>
      <Field label="From" id="designation-from" type="date" required value={from} onChange={(e) => setFrom(e.target.value)} />
      <Select label="Household income up to" id="designation-percent" value={percent}
        onChange={(e) => setPercent(e.target.value)}>
        {INCOME_PERCENTS.map((p) => <option key={p} value={String(p)}>{`${p}% of median`}</option>)}
        <option value="">Not a tax credit unit</option>
      </Select>
      {!ending && (
        <>
          <Field label="Bedrooms" id="designation-bedrooms" type="number" min={0} max={6} required value={bedrooms}
            onChange={(e) => setBedrooms(e.target.value)} />
          <Field label="Utility allowance a month" id="designation-allowance" inputMode="decimal" required
            value={allowance} onChange={(e) => setAllowance(e.target.value)} />
          <p className="text-sm text-ink-muted">
            The rent and the allowance together may be 30% of the income limit for the household the bedrooms
            imply: one person with no bedroom, one and a half people a bedroom.
          </p>
        </>
      )}
      <div><Button type="submit">Save tax credit</Button></div>
    </Form>
  );
}

// On a lease's page, while its unit is a tax credit unit: where the lease stands as the
// compliance report says it, its household's certifications, and Record certification.
export function TaxCreditLeaseCard({ leaseId }: { leaseId: string }) {
  const [standing, setStanding] = useState<LeaseTaxCredit | null | undefined>(undefined);
  const [assistance, setAssistance] = useState<LeaseAssistance | null>(null);
  const [error, setError] = useState("");
  const [recording, setRecording] = useState(false);
  function refresh() {
    api.get<LeaseTaxCredit | null>(`/api/tax-credit/leases/${leaseId}`).then(setStanding)
      .catch((failed) => setError(detail(failed)));
    api.get<LeaseAssistance>(`/api/assistance/leases/${leaseId}`).then(setAssistance).catch(() => setAssistance(null));
  }
  useEffect(refresh, [leaseId]);
  const { said, error: refused, record } = useRecordThenClose(refresh, () => setRecording(false));

  if (error) return <Notice tone="error">{error}</Notice>;
  if (!standing) return null;
  return (
    <Card title="Tax credit">
      <div className="flex flex-col gap-3">
        <Said>{said}</Said>
        <p className="text-base">
          {`A tax credit unit at ${standing.income_percent}% of median, ${bedroomsLabel(standing.bedrooms)}.`}
        </p>
        <dl className="grid grid-cols-1 gap-3 text-base sm:grid-cols-3">
          <div>
            <dt className="text-sm text-ink-muted">Certification</dt>
            <dd data-testid="tax-credit-certification">
              <Badge tone={standing.certification === "current" ? "positive" : "caution"}>{standing.certification}</Badge>
            </dd>
          </div>
          <div>
            <dt className="text-sm text-ink-muted">Income test</dt>
            <dd data-testid="tax-credit-income">
              <Badge tone={incomeTone(standing.income_test)}>{standing.income_test}</Badge>
            </dd>
          </div>
          <div>
            <dt className="text-sm text-ink-muted">Rent test</dt>
            <dd data-testid="tax-credit-rent">
              <Badge tone={rentTone(standing.rent_test)}>{standing.rent_test}</Badge>
            </dd>
            <dd className="text-sm text-ink-muted">{grossLine(standing)}</dd>
          </div>
        </dl>
        {assistance !== null && <CertificationsTable certifications={assistance.compliance.certifications} />}
        <div><Button variant="secondary" onClick={() => setRecording(true)}>Record certification</Button></div>
      </div>
      {recording && (
        <SidePanel title="Record an income certification" onClose={() => setRecording(false)}>
          <CertificationForm leaseId={leaseId} record={record}
            note={"The household's income and size as the tenant income certification gives them: at move-in, every " +
              "year, or an interim one between. The PMS files nothing with the state agency."} />
          <Notice tone="error">{refused}</Notice>
        </SidePanel>
      )}
    </Card>
  );
}
