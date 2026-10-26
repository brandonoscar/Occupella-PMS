import { useState } from "react";
import { Button } from "../components/Button";
import { Field } from "../components/Field";
import { Form } from "../components/Form";
import { Notice } from "../components/Notice";
import { Said } from "../components/Said";
import { Card } from "../components/Shell";
import { SidePanel } from "../components/SidePanel";
import { money } from "../money";
import type { UnitDetails } from "../unitDetails";
import { detailsBody, detailsForm, sizeLine } from "../unitDetails";
import { useRecord } from "./useRecord";

// A unit's details (migration 0124): its bedrooms, bathrooms, square feet and market rent, which
// staff keep here; the property's page, the rent roll and Listings show them.

// "2 beds, 1.5 baths, 850 sq ft" under a unit's name in a table; nothing when none is recorded.
export function UnitSize({ details }: { details: UnitDetails }) {
  const line = sizeLine(details);
  return line ? <span className="block text-sm text-ink-muted" data-testid="unit-size">{line}</span> : null;
}

// A market rent in a table's cell, blank when not recorded.
export function MarketRent({ details }: { details: UnitDetails }) {
  return <>{details.market_rent === null ? "" : money(details.market_rent)}</>;
}

const NOT_RECORDED = "Not recorded";

export function UnitDetailsCard({ unitId, details, refresh }: {
  unitId: string;
  details: UnitDetails;
  refresh: () => void;
}) {
  const [editing, setEditing] = useState(false);
  const { said, error, record } = useRecord(refresh);
  const rows: [string, string][] = [
    ["Bedrooms", details.bedrooms === null ? NOT_RECORDED : details.bedrooms === 0 ? "Studio" : String(details.bedrooms)],
    ["Bathrooms", details.bathrooms ?? NOT_RECORDED],
    ["Square feet", details.square_feet === null ? NOT_RECORDED : details.square_feet.toLocaleString("en-US")],
    ["Market rent", details.market_rent === null ? NOT_RECORDED : money(details.market_rent)],
  ];
  return (
    <Card title="Details">
      <div className="flex flex-col gap-3">
        <Said>{said}</Said>
        <dl className="grid grid-cols-2 gap-3 text-base sm:grid-cols-4" aria-label="Unit details">
          {rows.map(([label, value]) => (
            <div key={label}>
              <dt className="text-sm text-ink-muted">{label}</dt>
              <dd className={value === NOT_RECORDED ? "text-ink-muted" : ""}>{value}</dd>
            </div>
          ))}
        </dl>
        <div><Button variant="secondary" onClick={() => setEditing(true)}>Edit details</Button></div>
      </div>
      {editing && (
        <SidePanel title="Edit details" onClose={() => setEditing(false)}>
          <DetailsForm details={details} onSave={async (body) => {
            const saved = await record<{ changed: boolean }>(`/api/units/${unitId}/details`, body,
              (answer) => (answer.changed ? "Details saved." : "Nothing changed."));
            if (saved) setEditing(false);
          }} />
          <Notice tone="error">{error}</Notice>
        </SidePanel>
      )}
    </Card>
  );
}

function DetailsForm({ details, onSave }: {
  details: UnitDetails;
  onSave: (body: ReturnType<typeof detailsBody>) => Promise<void>;
}) {
  const [form, setForm] = useState(detailsForm(details));
  const field = (name: keyof typeof form) => ({
    value: form[name],
    onChange: (event: { target: { value: string } }) => setForm({ ...form, [name]: event.target.value }),
  });
  return (
    <Form className="flex flex-col gap-3" onSubmit={(event) => {
      event.preventDefault();
      return onSave(detailsBody(form));
    }}>
      <p className="text-sm text-ink-muted">
        Leave out what isn&apos;t known. A studio has 0 bedrooms; a half bath counts as a half (1.5).
      </p>
      <Field label="Bedrooms" id="unit-bedrooms" inputMode="numeric" {...field("bedrooms")} />
      <Field label="Bathrooms" id="unit-bathrooms" inputMode="decimal" {...field("bathrooms")} />
      <Field label="Square feet" id="unit-square-feet" inputMode="numeric" {...field("square_feet")} />
      <Field label="Market rent a month" id="unit-market-rent" inputMode="decimal" {...field("market_rent")} />
      <div><Button type="submit"
        says="Saves them; the old ones stay in the history. Listings show them; no lease's rent changes.">Save details</Button></div>
    </Form>
  );
}
