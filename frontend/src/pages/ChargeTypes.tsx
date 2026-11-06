import { useEffect, useState } from "react";
import { ApiError, api } from "../api";
import { Select } from "../components/Select";
import type { Named } from "../setup";

// The company's own kinds of fee (migration 0100): an application fee, a key replaced, pet rent.
// A fee of a type is counted on a line of its own in the owners' income statements. Settings,
// Chart of accounts adds, renames and archives them (ChartOfAccountsPage); an archived one isn't
// listed here, so no new fee takes it.
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
