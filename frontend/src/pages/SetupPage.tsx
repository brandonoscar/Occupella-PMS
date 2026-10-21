import { useEffect, useState } from "react";
import type { Me } from "../api";
import { ApiError, api } from "../api";
import { Button } from "../components/Button";
import { Field } from "../components/Field";
import { Form } from "../components/Form";
import { Notice } from "../components/Notice";
import { Said } from "../components/Said";
import { Card, PageHeader, Shell } from "../components/Shell";
import { SidePanel } from "../components/SidePanel";
import type { Company } from "../company";
import { addressLines, logoHref } from "../company";
import { MAX_BYTES, toBase64, typeOf } from "../documents";
import { money } from "../money";
import { lateFeeSummary, nsfFeeSummary } from "../setup";
import { AutomaticEmailsCard } from "./AutomaticEmailsCard";
import { AutomaticFeesCard } from "./AutomaticFeesCard";
import { ScreeningCriteriaCard } from "./ScreeningCriteriaCard";
import { STAFF_NAV } from "./staffNav";
import { useRecord } from "./useRecord";

// Settings → Company setup (ADR 0006, step 12): the company itself, its name, mailing address and
// logo (which owners and tenants see in their portal), the late fee and NSF fee terms each
// property added from now on starts with, and the emails the PMS writes by itself (ADR 0007). Everything else set up once lives where it is used:
// bank accounts on Banking, owners on Owners, vendors on Vendors, properties, units, leases in
// place and their terms on Properties and each property's page, expense accounts in Chart of
// accounts.
export function SetupPage({ me }: { me: Me }) {
  const [company, setCompany] = useState<Company | null>(null);
  const [error, setError] = useState("");
  const [open, setOpen] = useState<"" | "company" | "fees">("");
  const [runs, setRuns] = useState(0);
  const refresh = () => setRuns((n) => n + 1);
  const { said, error: refused, record } = useRecord(refresh);
  useEffect(() => {
    api.get<Company>("/api/company").then(setCompany)
      .catch((failed) => setError(failed instanceof ApiError ? failed.detail : String(failed)));
  }, [runs]);
  const close = () => setOpen("");

  return (
    <Shell me={me} nav={STAFF_NAV}>
      <PageHeader title="Company setup" />
      <Said>{said}</Said>
      {company === null ? (
        <Notice tone={error ? "error" : "info"}>{error || "Loading"}</Notice>
      ) : (
        <>
          <Card title="Company">
            <address className="text-base not-italic" aria-label="Company">
              <span className="block font-medium">{company.name}</span>
              {company.address
                ? addressLines(company.address).map((line) => <span key={line} className="block text-ink-muted">{line}</span>)
                : <span className="block text-ink-muted">No mailing address yet</span>}
            </address>
            <p className="mt-2 text-sm text-ink-muted" data-testid="reply-to">
              {company.reply_to ? `Replies to its emails go to ${company.reply_to}.` : "Replies to its emails go to the server's From address."}
            </p>
            <div className="mt-3"><Button variant="secondary" onClick={() => setOpen("company")}>Edit company</Button></div>
          </Card>
          <LogoCard company={company} refresh={refresh} />
          <Card title="New properties start with">
            <dl className="grid grid-cols-1 gap-3 text-base sm:grid-cols-2">
              <div>
                <dt className="text-sm text-ink-muted">Late fees</dt>
                <dd data-testid="default-late-fees">
                  {company.late_fees ? lateFeeSummary({ ...company.late_fees, starts_on: "" }) : "No late fee terms"}
                </dd>
              </div>
              <div>
                <dt className="text-sm text-ink-muted">NSF fee</dt>
                <dd data-testid="default-nsf-fee">
                  {company.nsf_fee === null ? "No NSF fee" : nsfFeeSummary({ starts_on: "", fee: company.nsf_fee })}
                </dd>
              </div>
            </dl>
            <div className="mt-3"><Button variant="secondary" onClick={() => setOpen("fees")}>Set default fees</Button></div>
          </Card>
          <AutomaticFeesCard />
          <AutomaticEmailsCard />
          <ScreeningCriteriaCard />
          {open === "company" && (
            <SidePanel title="Edit company" onClose={close}>
              <CompanyForm company={company} save={(body, done) => record("/api/company", body, () => done).then((ok) => { if (ok) close(); })} />
              <Notice tone="error">{refused}</Notice>
            </SidePanel>
          )}
          {open === "fees" && (
            <SidePanel title="Set default fees" onClose={close}>
              <FeesForm company={company} save={(body, done) => record("/api/company", body, () => done).then((ok) => { if (ok) close(); })} />
              <Notice tone="error">{refused}</Notice>
            </SidePanel>
          )}
        </>
      )}
    </Shell>
  );
}

type Save = (body: unknown, done: string) => Promise<void>;
const blank = (text: string) => (text.trim() === "" ? null : text.trim());

// What the save sends besides the part a form changes: the rest as it stands.
function standing(company: Company) {
  return {
    name: company.name, address: company.address, late_fees: company.late_fees, nsf_fee: company.nsf_fee,
    reply_to: company.reply_to,
  };
}

function CompanyForm({ company, save }: { company: Company; save: Save }) {
  const [name, setName] = useState(company.name);
  const [replyTo, setReplyTo] = useState(company.reply_to ?? "");
  const [address, setAddress] = useState({
    address_line_1: company.address?.address_line_1 ?? "",
    address_line_2: company.address?.address_line_2 ?? "",
    city: company.address?.city ?? "",
    state: company.address?.state ?? "",
    postal_code: company.address?.postal_code ?? "",
  });
  const set = (key: keyof typeof address) => (e: React.ChangeEvent<HTMLInputElement>) => setAddress({ ...address, [key]: e.target.value });
  const given = [address.address_line_1, address.city, address.state, address.postal_code].some((part) => part.trim());
  return (
    <Form className="flex flex-col gap-3" onSubmit={(event) => {
      event.preventDefault();
      return save({
        ...standing(company),
        name,
        reply_to: blank(replyTo),
        address: given
          ? { ...address, address_line_2: blank(address.address_line_2) }
          : null,
      }, `Saved ${name.trim()}.`);
    }}>
      <Field label="Company name" id="company-name" required maxLength={200} value={name} onChange={(e) => setName(e.target.value)} />
      <Field label="Street address" id="company-line-1" required={given} maxLength={200} value={address.address_line_1} onChange={set("address_line_1")} />
      <Field label="Suite or floor (optional)" id="company-line-2" maxLength={200} value={address.address_line_2} onChange={set("address_line_2")} />
      <Field label="City" id="company-city" required={given} maxLength={100} value={address.city} onChange={set("city")} />
      <Field label="State" id="company-state" required={given} maxLength={50} value={address.state} onChange={set("state")} />
      <Field label="ZIP code" id="company-zip" required={given} maxLength={20} value={address.postal_code} onChange={set("postal_code")} />
      <Field label="Replies to the company's emails go to (optional)" id="company-reply-to" type="email" maxLength={254}
        value={replyTo} onChange={(e) => setReplyTo(e.target.value)} />
      <div><Button type="submit">Save</Button></div>
    </Form>
  );
}

function FeesForm({ company, save }: { company: Company; save: Save }) {
  const late = company.late_fees;
  const [charging, setCharging] = useState(late !== null);
  const [fees, setFees] = useState({
    grace_days: String(late?.grace_days ?? 5),
    flat_fee: late?.flat_fee ?? "",
    percent: late && Number(late.percent) > 0 ? late.percent : "",
    maximum: late?.maximum ?? "",
  });
  const [nsf, setNsf] = useState(company.nsf_fee ?? "");
  const set = (key: keyof typeof fees) => (e: React.ChangeEvent<HTMLInputElement>) => setFees({ ...fees, [key]: e.target.value });
  return (
    <Form className="flex flex-col gap-3" onSubmit={(event) => {
      event.preventDefault();
      return save({
        ...standing(company),
        late_fees: charging
          ? { grace_days: Number(fees.grace_days), flat_fee: blank(fees.flat_fee) ?? "0", percent: blank(fees.percent) ?? "0", maximum: blank(fees.maximum) }
          : null,
        nsf_fee: blank(nsf),
      }, "Saved the fees new properties start with.");
    }}>
      <label className="flex items-center gap-2 text-sm text-ink">
        <input type="checkbox" checked={charging} onChange={(e) => setCharging(e.target.checked)} />
        Charge late fees
      </label>
      {charging && (
        <>
          <Field label="Grace days" id="default-grace" inputMode="numeric" required value={fees.grace_days} onChange={set("grace_days")} />
          <Field label="Flat fee" id="default-flat" inputMode="decimal" value={fees.flat_fee} onChange={set("flat_fee")} />
          <Field label="Percent of unpaid" id="default-percent" inputMode="decimal" value={fees.percent} onChange={set("percent")} />
          <Field label="Up to" id="default-maximum" inputMode="decimal" value={fees.maximum} onChange={set("maximum")} />
        </>
      )}
      <Field label="NSF fee (empty for none)" id="default-nsf" inputMode="decimal" value={nsf} onChange={(e) => setNsf(e.target.value)} />
      <p className="text-sm text-ink-muted">
        {`A property already here keeps its own terms${nsf.trim() ? `; a new one charges ${money(nsf.trim())} for a returned payment` : ""}.`}
      </p>
      <div><Button type="submit">Save</Button></div>
    </Form>
  );
}

// The logo, a PNG or JPEG of at most 5 MB, which owners and tenants see in their portal.
function LogoCard({ company, refresh }: { company: Company; refresh: () => void }) {
  const { said, error, record } = useRecord(refresh);
  const [problem, setProblem] = useState("");
  return (
    <Card title="Logo">
      {company.logo ? (
        <img src={logoHref(company.logo)} alt={`${company.name} logo`} className="mb-3 h-16 w-auto max-w-60 object-contain" />
      ) : (
        <p className="mb-3 text-base text-ink-muted">No logo yet</p>
      )}
      <div className="flex flex-wrap items-center gap-3">
        <label htmlFor="company-logo" className="flex flex-col gap-1.5 text-sm text-ink-muted">
          {company.logo ? "Replace the logo" : "Upload a logo"}
          <input id="company-logo" type="file" accept=".png,.jpg,.jpeg" className="text-sm text-ink"
            onChange={async (e) => {
              const picked = e.target.files?.[0];
              setProblem("");
              if (!picked) return;
              const type = typeOf(picked);
              if (type !== "image/png" && type !== "image/jpeg") return setProblem("A logo is a PNG or a JPEG.");
              if (picked.size > MAX_BYTES) return setProblem("A logo is at most 5 MB.");
              const base64 = toBase64(new Uint8Array(await picked.arrayBuffer()));
              await record("/api/company/logo", { content_type: type, content_base64: base64 }, () => "The logo is saved.");
            }} />
        </label>
        {company.logo && (
          <Button variant="ghost" onClick={() => record("/api/company/logo/remove", {}, () => "The logo is removed.")}>Remove logo</Button>
        )}
      </div>
      <Said>{said}</Said>
      <Notice tone="error">{problem || error}</Notice>
    </Card>
  );
}
