import { useEffect, useState } from "react";
import { ApiError, api } from "../api";
import { Button } from "../components/Button";
import { Field } from "../components/Field";
import { Form } from "../components/Form";
import { Notice } from "../components/Notice";
import { Said } from "../components/Said";
import { Select } from "../components/Select";
import { Card } from "../components/Shell";
import { SidePanel } from "../components/SidePanel";
import { usDate } from "../money";
import type { OnFile, TaxKind, Whole } from "../taxIds";
import { nineDigits, taxIdLine } from "../taxIds";
import { useRecord } from "./useRecord";

// An owner's or vendor's tax ID (ADR 0010 phase 2, part F): its last four for every staff
// member, and for an admin Reveal, which shows it whole until they hide it or leave the page;
// each time is recorded with their name. A new one replaces the one before from then on.
export function TaxIdCard({ kind, id, name }: { kind: "owner" | "vendor"; id: string; name: string }) {
  const path = `/api/tax-ids/${kind}s/${id}`;
  const [onFile, setOnFile] = useState<OnFile | null>(null);
  const [error, setError] = useState("");
  const [editing, setEditing] = useState(false);
  const [whole, setWhole] = useState<Whole | null>(null);
  const [runs, setRuns] = useState(0);
  const { said, error: refused, record } = useRecord(() => setRuns((n) => n + 1));
  useEffect(() => {
    setWhole(null);
    api.get<OnFile>(path).then(setOnFile)
      .catch((failed) => setError(failed instanceof ApiError ? failed.detail : String(failed)));
  }, [path, runs]);

  async function reveal(taxId: string) {
    setError("");
    try {
      setWhole(await api.get<Whole>(`/api/tax-ids/${taxId}/whole`));
    } catch (failed) {
      setError(failed instanceof ApiError ? failed.detail : String(failed));
    }
  }

  const held = onFile?.tax_id ?? null;
  return (
    <Card title="Tax ID">
      <Said>{said}</Said>
      {onFile === null ? (
        <Notice tone={error ? "error" : "info"}>{error || "Loading"}</Notice>
      ) : (
        <div className="flex flex-col gap-3">
          <dl className="grid grid-cols-[max-content_1fr] gap-x-6 gap-y-1 text-base" aria-label={`Tax ID, ${name}`}>
            <dt className="text-ink-muted">Tax ID</dt>
            <dd data-testid="tax-id">
              {held === null ? "None on file" : whole ? `${whole.kind} ${whole.number}` : taxIdLine(held)}
            </dd>
            {held && (
              <>
                <dt className="text-ink-muted">Recorded</dt>
                <dd>{usDate(held.recorded_at.slice(0, 10))}{held.recorded_by ? ` by ${held.recorded_by}` : ""}</dd>
              </>
            )}
          </dl>
          {whole && <p className="text-sm text-ink-muted">Shown to you whole, and recorded with your name.</p>}
          <div className="flex flex-wrap gap-2">
            {held && onFile.may_reveal && (whole ? (
              <Button variant="secondary" onClick={() => setWhole(null)}>Hide</Button>
            ) : (
              <Button variant="secondary" says="Shows it whole until you hide it or leave; each time is recorded with your name."
                onClick={() => void reveal(held.id)}>Reveal</Button>
            ))}
            <Button variant="secondary" onClick={() => setEditing(true)}>
              {held ? "Change tax ID" : "Add tax ID"}
            </Button>
          </div>
          {held && !onFile.may_reveal && (
            <p className="text-sm text-ink-muted">Only an admin sees a tax ID whole.</p>
          )}
          <Notice tone="error">{editing ? "" : refused || error}</Notice>
          {editing && (
            <SidePanel title={`Tax ID, ${name}`} onClose={() => setEditing(false)}>
              <TaxIdForm save={(body) =>
                record(path, body, () => `Saved ${name}'s tax ID.`).then((ok) => {
                  if (ok) setEditing(false);
                })} />
              <Notice tone="error">{refused}</Notice>
            </SidePanel>
          )}
        </div>
      )}
    </Card>
  );
}

function TaxIdForm({ save }: { save: (body: unknown) => Promise<void> }) {
  const [kind, setKind] = useState<TaxKind>("EIN");
  const [number, setNumber] = useState("");
  const [wrong, setWrong] = useState("");
  return (
    <Form className="flex flex-col gap-3" onSubmit={(event) => {
      event.preventDefault();
      const digits = nineDigits(number);
      if (digits === null) {
        setWrong("A tax ID is nine digits: XX-XXXXXXX for an EIN, XXX-XX-XXXX for an SSN.");
        return Promise.resolve();
      }
      setWrong("");
      return save({ kind, number: digits });
    }}>
      <Select label="Kind" id="tax-id-kind" value={kind} onChange={(e) => setKind(e.target.value as TaxKind)}>
        <option value="EIN">EIN (a business)</option>
        <option value="SSN">SSN (a person)</option>
      </Select>
      <Field label="Number" id="tax-id-number" required inputMode="numeric" maxLength={11} autoComplete="off"
        placeholder={kind === "EIN" ? "XX-XXXXXXX" : "XXX-XX-XXXX"} value={number}
        onChange={(e) => setNumber(e.target.value)} />
      <Notice tone="error">{wrong}</Notice>
      <p className="text-sm text-ink-muted">
        Kept sealed and shown by its last four. Only an admin sees it whole, and only it goes into the year's 1099 export.
      </p>
      <div>
        <Button type="submit" says="It replaces the one on file from now on; the old one stays in the history. Nothing is sent to them.">
          Save
        </Button>
      </div>
    </Form>
  );
}
