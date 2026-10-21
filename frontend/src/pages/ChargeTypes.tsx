import { Tags } from "lucide-react";
import { useEffect, useState } from "react";
import { ApiError, api } from "../api";
import { Button } from "../components/Button";
import { EmptyState } from "../components/EmptyState";
import { Field } from "../components/Field";
import { Form } from "../components/Form";
import { Notice } from "../components/Notice";
import { Select } from "../components/Select";
import { Card } from "../components/Shell";
import { SidePanel } from "../components/SidePanel";
import type { Named } from "../setup";
import { useRecord } from "./useRecord";

// The company's own kinds of fee (migration 0100): an application fee, a key replaced, pet rent.
// A fee of a type is counted on a line of its own in the owners' income statements.
export function useChargeTypes(): [Named[] | null, string, () => void] {
  const [types, setTypes] = useState<Named[] | null>(null);
  const [error, setError] = useState("");
  function load() {
    api.get<Named[]>("/api/setup/charge-types")
      .then(setTypes)
      .catch((failed) => setError(failed instanceof ApiError ? failed.detail : String(failed)));
  }
  useEffect(load, []);
  return [types, error, load];
}

// The type of a fee, picked from the company's; nothing shows until it has one.
export function ChargeTypeSelect({ id, types, value, onChange }: {
  id: string;
  types: Named[] | null;
  value: string;
  onChange: (value: string) => void;
}) {
  if (!types || types.length === 0) return null;
  return (
    <Select label="Type" id={id} value={value} onChange={(e) => onChange(e.target.value)}>
      <option value="">None</option>
      {types.map((t) => <option key={t.id} value={t.id}>{t.name}</option>)}
    </Select>
  );
}

// Settings → Chart of accounts: the charge types, with Add charge type in a side panel.
export function ChargeTypesCard({ say }: { say: (said: string) => void }) {
  const [types, error, reload] = useChargeTypes();
  const [adding, setAdding] = useState(false);
  return (
    <Card title="Charge types">
      <div className="mb-3 flex flex-wrap items-center justify-between gap-3">
        <p className="text-base text-ink-muted">
          Fees of a type count on a line of their own in the owners&apos; income statements,
          apart from the other charges.
        </p>
        <Button variant="secondary" onClick={() => setAdding(true)}>Add charge type</Button>
      </div>
      {types === null ? (
        <Notice tone={error ? "error" : "info"}>{error || "Loading"}</Notice>
      ) : types.length === 0 ? (
        <EmptyState icon={Tags} title="No charge types yet" />
      ) : (
        <ul aria-label="Charge types" className="flex flex-col gap-1 text-base">
          {types.map((t) => <li key={t.id}>{t.name}</li>)}
        </ul>
      )}
      {adding && <AddChargeTypePanel say={say} refresh={reload} close={() => setAdding(false)} />}
    </Card>
  );
}

function AddChargeTypePanel({ say, refresh, close }: {
  say: (said: string) => void;
  refresh: () => void;
  close: () => void;
}) {
  const { error, record } = useRecord(refresh, say);
  const [name, setName] = useState("");
  return (
    <SidePanel title="Add charge type" onClose={close}>
      <Form className="flex flex-col gap-3" onSubmit={async (event) => {
        event.preventDefault();
        if (await record("/api/setup/charge-types", { name }, () => `Added ${name.trim()}.`)) close();
      }}>
        <Field label="Charge type" id="charge-type-name" required maxLength={100} value={name}
          onChange={(e) => setName(e.target.value)} />
        <div><Button type="submit">Add charge type</Button></div>
        <Notice tone="error">{error}</Notice>
      </Form>
    </SidePanel>
  );
}
