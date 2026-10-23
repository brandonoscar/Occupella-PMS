import { FileText } from "lucide-react";
import { useEffect, useState } from "react";
import type { Me } from "../api";
import { ApiError, api } from "../api";
import { Badge } from "../components/Badge";
import { Button, LinkButton } from "../components/Button";
import { EmptyState } from "../components/EmptyState";
import { Field } from "../components/Field";
import { Form } from "../components/Form";
import { Notice } from "../components/Notice";
import { Said } from "../components/Said";
import { Select } from "../components/Select";
import { Card, PageHeader, Shell } from "../components/Shell";
import { SidePanel } from "../components/SidePanel";
import { Wide } from "../components/Wide";
import { money, usDate } from "../money";
import { GoLink } from "./NeedsAttention";
import { STAFF_NAV } from "./staffNav";

export type TaxpayerId = {
  tin_type: "EIN" | "SSN" | "ITIN";
  tin_last4: string;
  legal_name: string;
  exempt: boolean;
  recorded_by: string | null;
  recorded_at: string;
};
export type Party = {
  kind: "vendor" | "owner" | "company";
  id: string | null;
  name: string;
  taxpayer_id: TaxpayerId | null;
  address: string[] | null;
  missing: string[];
};
export type Recipient = Party & { form: "1099-NEC" | "1099-MISC"; amount: string };
export type TaxForms = { year: number; threshold: string; payer: Party; recipients: Recipient[]; sealing: boolean };

// "XXX-XX-1234" or "XX-XXX4567": the number as the PMS shows it, its last four digits.
export function tinCut(id: TaxpayerId): string {
  return id.tin_type === "EIN" ? `XX-XXX${id.tin_last4}` : `XXX-XX-${id.tin_last4}`;
}

// "Needs a taxpayer ID and a mailing address", or "Ready".
export function needs(party: Party): string {
  return party.missing.length === 0 ? "Ready" : `Needs ${party.missing.map((m) => `a ${m}`).join(" and ")}`;
}

const FORMS = [
  { form: "1099-NEC", key: "nec", title: "1099-NEC: vendors", box: "Box 1, nonemployee compensation" },
  { form: "1099-MISC", key: "misc", title: "1099-MISC: owners' rents", box: "Box 1, rents" },
] as const;

const HREF = { vendor: "/vendors/", owner: "/owners/", company: "/settings/setup" };

// Accounting → 1099 forms (migration 0162): the year's recipients of a 1099-NEC (vendors marked
// for one, paid the year's threshold) and a 1099-MISC (owners' rents), what each form still needs,
// the taxpayer IDs from W-9s (sealed; the last four digits shown), each recipient's copy as a PDF
// and, for an admin, the list to upload when filing. The PMS files nothing itself.
export function TaxFormsPage({ me, year: start }: { me: Me; year?: number }) {
  const thisYear = new Date().getUTCFullYear();
  const [year, setYear] = useState(start ?? thisYear - 1);
  const [view, setView] = useState<TaxForms | null>(null);
  const [error, setError] = useState("");
  const [editing, setEditing] = useState<Party | null>(null);
  const [said, setSaid] = useState("");
  const [runs, setRuns] = useState(0);
  const admin = me.roles?.includes("admin") ?? false;
  useEffect(() => {
    let live = true;
    setView(null);
    api.get<TaxForms>(`/api/tax-forms?year=${year}`)
      .then((found) => { if (live) setView(found); })
      .catch((failed) => { if (live) setError(failed instanceof ApiError ? failed.detail : String(failed)); });
    return () => { live = false; };
  }, [year, runs]);

  return (
    <Shell me={me} nav={STAFF_NAV}>
      <PageHeader title="1099 forms" />
      <Said>{said}</Said>
      <div className="flex flex-wrap items-end gap-3">
        <Select label="Tax year" id="tax-year" value={year} onChange={(e) => setYear(Number(e.target.value))}>
          {[0, 1, 2, 3].map((back) => <option key={back} value={thisYear - back}>{thisYear - back}</option>)}
        </Select>
      </div>
      {view === null ? (
        <Notice tone={error ? "error" : "info"}>{error || "Loading"}</Notice>
      ) : (
        <>
          {!view.sealing && (
            <Notice tone="info">This server has no key to seal taxpayer IDs (PMS_FIELD_KEY): none can be kept or read until it has one.</Notice>
          )}
          <p className="text-base text-ink-muted">
            {`A 1099 goes to each vendor marked for one and paid ${money(view.threshold)} or more in ${view.year}, and to each owner whose rents came to as much, unless marked exempt. Each recipient's copy shows only the last four digits of their number. The filing list is laid out for the IRS's IRIS portal; check it against IRIS's own template before you upload it. The PMS files nothing itself.`}
          </p>
          <Card title="Payer">
            <PartyLine party={view.payer} />
            <div className="mt-3">
              <Button variant="secondary" onClick={() => setEditing(view.payer)}>Set the company&apos;s EIN</Button>
            </div>
          </Card>
          {FORMS.map(({ form, key, title, box }) => {
            const listed = view.recipients.filter((r) => r.form === form);
            return (
              <Card key={form} title={title}>
                {listed.length === 0 ? (
                  <EmptyState icon={FileText} title={`No ${form} for ${view.year}`} />
                ) : (
                  <>
                    <Wide>
                      <table className="w-full text-base" aria-label={title}>
                        <thead>
                          <tr>
                            <th className="text-left">Recipient</th>
                            <th className="text-right">{box}</th>
                            <th className="text-left">Taxpayer ID</th>
                            <th className="text-left">Mailing address</th>
                            <th className="text-left">Form</th>
                            <th><span className="sr-only">Actions</span></th>
                          </tr>
                        </thead>
                        <tbody>
                          {listed.map((r) => (
                            <tr key={`${r.kind}-${r.id}`}>
                              <td><GoLink href={`${HREF[r.kind]}${r.id}`}>{r.name}</GoLink></td>
                              <td className="text-right">{money(r.amount)}</td>
                              <td>{r.taxpayer_id ? `${r.taxpayer_id.legal_name}, ${tinCut(r.taxpayer_id)}` : "None on file"}</td>
                              <td>{r.address ? r.address.join(", ") : "None on file"}</td>
                              <td><Badge tone={r.missing.length === 0 ? "positive" : "caution"}>{needs(r)}</Badge></td>
                              <td className="text-right">
                                <Button size="sm" variant="secondary" onClick={() => setEditing(r)}>{`Taxpayer ID of ${r.name}`}</Button>
                              </td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </Wide>
                    <div className="mt-3 flex flex-wrap gap-2">
                      <LinkButton href={`/api/tax-forms/${view.year}/copies.pdf?form=${key}`}>Recipient copies (PDF)</LinkButton>
                      {admin && <LinkButton href={`/api/tax-forms/${view.year}/filing.csv?form=${key}`}>Filing list (CSV)</LinkButton>}
                    </div>
                  </>
                )}
              </Card>
            );
          })}
          {editing && (
            <SidePanel title={editing.kind === "company" ? "The company's EIN" : `Taxpayer ID of ${editing.name}`} onClose={() => setEditing(null)}>
              <TaxpayerIdForm party={editing} save={async (body) => {
                setSaid("");
                try {
                  await api.post("/api/tax-forms/taxpayer-id", body);
                  setSaid(`Kept the taxpayer ID of ${editing.kind === "company" ? "the company" : editing.name}.`);
                  setEditing(null);
                  setRuns((n) => n + 1);
                  return "";
                } catch (failed) {
                  return failed instanceof ApiError ? failed.detail : String(failed);
                }
              }} />
            </SidePanel>
          )}
        </>
      )}
    </Shell>
  );
}

function PartyLine({ party }: { party: Party }) {
  const id = party.taxpayer_id;
  return (
    <>
    <dl className="grid grid-cols-1 gap-3 text-base sm:grid-cols-3" aria-label="Payer">
      <div>
        <dt className="text-sm text-ink-muted">Name and EIN</dt>
        <dd>{id ? `${id.legal_name}, ${tinCut(id)}` : "None on file"}</dd>
      </div>
      <div>
        <dt className="text-sm text-ink-muted">Mailing address</dt>
        <dd>{party.address ? party.address.join(", ") : <GoLink href="/settings/setup">None on file: add it in Company setup</GoLink>}</dd>
      </div>
      <div>
        <dt className="text-sm text-ink-muted">Forms</dt>
        <dd data-testid="payer-ready">{needs(party)}</dd>
      </div>
    </dl>
    {id && <p className="mt-2 text-sm text-ink-subtle">{`Kept ${usDate(id.recorded_at)}${id.recorded_by ? ` by ${id.recorded_by}` : ""}.`}</p>}
    </>
  );
}

function TaxpayerIdForm({ party, save }: {
  party: Party;
  save: (body: unknown) => Promise<string>;
}) {
  const company = party.kind === "company";
  const [tinType, setTinType] = useState<string>(party.taxpayer_id?.tin_type ?? (company ? "EIN" : "SSN"));
  const [tin, setTin] = useState("");
  const [name, setName] = useState(party.taxpayer_id?.legal_name ?? party.name);
  const [exempt, setExempt] = useState(party.taxpayer_id?.exempt ?? false);
  const [error, setError] = useState("");
  return (
    <Form className="flex flex-col gap-3" onSubmit={async (event) => {
      event.preventDefault();
      setError(await save({
        about: party.kind,
        ...(company ? {} : { about_id: party.id }),
        tin_type: tinType,
        tin: tin.trim(),
        legal_name: name.trim(),
        exempt: party.kind === "owner" && exempt,
      }));
    }}>
      <p className="text-sm text-ink-muted">
        As the W-9 gives them. The number is sealed before it is kept; the PMS shows only its last four digits.
      </p>
      <Field label="Name (W-9 line 1)" id="tin-name" required maxLength={80} value={name} onChange={(e) => setName(e.target.value)} />
      <Select label="Kind of number" id="tin-type" value={tinType} disabled={company} onChange={(e) => setTinType(e.target.value)}>
        <option value="EIN">EIN</option>
        <option value="SSN">SSN</option>
        <option value="ITIN">ITIN</option>
      </Select>
      <Field label="Taxpayer ID" id="tin-number" required inputMode="numeric" autoComplete="off" maxLength={11} value={tin}
        onChange={(e) => setTin(e.target.value)} />
      {party.kind === "owner" && (
        <label className="flex items-center gap-2 text-sm text-ink">
          <input type="checkbox" checked={exempt} onChange={(e) => setExempt(e.target.checked)} />
          Exempt: the company files no 1099 for them (a corporation, say)
        </label>
      )}
      <Notice tone="error">{error}</Notice>
      <div><Button type="submit">Keep it</Button></div>
    </Form>
  );
}
