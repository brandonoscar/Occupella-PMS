import { Truck } from "lucide-react";
import { useEffect, useState } from "react";
import type { Me } from "../api";
import { ApiError, api } from "../api";
import { Badge } from "../components/Badge";
import { Button } from "../components/Button";
import { EmptyState } from "../components/EmptyState";
import { Field } from "../components/Field";
import { Form } from "../components/Form";
import { Notice } from "../components/Notice";
import { Pager } from "../components/Pager";
import { Select } from "../components/Select";
import { PageHeader, Shell } from "../components/Shell";
import { SidePanel } from "../components/SidePanel";
import { Wide } from "../components/Wide";
import { latest } from "../latest";
import type { Paged } from "../rentals";
import { navigate } from "../router";
import type { PaymentMethod, VendorRow } from "../vendors";
import { PAYMENT, standingTone, standingWord } from "../vendors";
import { useRecord } from "./useRecord";
import { STAFF_NAV } from "./staffNav";
import { GoLink } from "./NeedsAttention";

// Maintenance → Vendors (ADR 0006, Buildium's): every vendor a page at a time, by name: trade,
// phone, email, W-9 on file or missing, and how their insurance stands (by the coverage that runs
// out first); the row opens their page. Add vendor opens a side panel.
export function VendorsPage({ me }: { me: Me }) {
  const [number, setNumber] = useState(1);
  const [page, setPage] = useState<Paged<VendorRow> | null>(null);
  const [error, setError] = useState("");
  const [adding, setAdding] = useState(false);

  useEffect(() => {
    const run = latest();
    api
      .get<Paged<VendorRow>>(`/api/vendors?page=${number}`)
      .then(run.keep(setPage))
      .catch(run.keep((failed) => setError(failed instanceof ApiError ? failed.detail : String(failed))));
    return run.stop;
  }, [number]);

  return (
    <Shell me={me} nav={STAFF_NAV}>
      <PageHeader title="Vendors" actions={<Button onClick={() => setAdding(true)}>Add vendor</Button>} />
      <Notice tone="error">{error}</Notice>
      {page === null ? (
        !error && <Notice tone="info">Loading</Notice>
      ) : page.items.length === 0 ? (
        <EmptyState icon={Truck} title="No vendors yet" />
      ) : (
        <>
          <Wide>
            <table className="w-full text-base">
              <thead>
                <tr>
                  <th className="text-left">Vendor</th>
                  <th className="text-left">Trade</th>
                  <th className="text-left">Phone</th>
                  <th className="text-left">Email</th>
                  <th className="text-left">W-9</th>
                  <th className="text-left">Insurance</th>
                </tr>
              </thead>
              <tbody>
                {page.items.map((row) => (
                  <tr key={row.id} aria-label={row.name} className="cursor-pointer hover:bg-raised"
                    onClick={() => navigate(`/vendors/${row.id}`)}>
                    <td><GoLink href={`/vendors/${row.id}`} className="font-medium text-iris hover:text-iris-hover hover:underline">{row.name}</GoLink></td>
                    <td className="text-ink-muted">{row.trade}</td>
                    <td className="text-ink-muted">{row.phone}</td>
                    <td className="text-ink-muted">{row.email}</td>
                    <td><Badge tone={row.w9_on_file ? "positive" : "caution"}>{row.w9_on_file ? "On file" : "Missing"}</Badge></td>
                    <td><Badge tone={standingTone(row.insurance)} data-testid="standing">{standingWord(row.insurance)}</Badge></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </Wide>
          <Pager page={page} onPage={setNumber} />
        </>
      )}
      {adding && (
        <SidePanel title="Add vendor" onClose={() => setAdding(false)}>
          <AddVendorForm added={(id) => navigate(`/vendors/${id}`)} />
        </SidePanel>
      )}
    </Shell>
  );
}

// What a vendor's profile is, as a form fills it in.
export type ProfileDraft = { trade: string; gets1099: boolean; method: PaymentMethod | "" };

export function profileBody(draft: ProfileDraft) {
  return { trade: draft.trade.trim() || null, gets_1099: draft.gets1099, payment_method: draft.method || null };
}

export function ProfileFields({ draft, change }: { draft: ProfileDraft; change: (part: Partial<ProfileDraft>) => void }) {
  return (
    <>
      <Field label="Trade (optional)" id="vendor-trade" maxLength={100} value={draft.trade}
        onChange={(e) => change({ trade: e.target.value })} />
      <Select label="Paid by" id="vendor-method" value={draft.method}
        onChange={(e) => change({ method: e.target.value as PaymentMethod | "" })}>
        <option value="">Not set</option>
        {(Object.keys(PAYMENT) as PaymentMethod[]).map((m) => <option key={m} value={m}>{PAYMENT[m]}</option>)}
      </Select>
      <label className="flex items-center gap-2 text-sm text-ink">
        <input type="checkbox" checked={draft.gets1099} onChange={(e) => change({ gets1099: e.target.checked })} />
        1099 vendor
      </label>
    </>
  );
}

function AddVendorForm({ added }: { added: (id: string) => void }) {
  const [name, setName] = useState("");
  const [draft, setDraft] = useState<ProfileDraft>({ trade: "", gets1099: false, method: "" });
  const [email, setEmail] = useState("");
  const [phone, setPhone] = useState("");
  const { error, record } = useRecord(() => undefined);
  return (
    <Form className="flex flex-col gap-3" onSubmit={(event) => {
      event.preventDefault();
      const contact = { ...(email.trim() ? { email: email.trim() } : {}), ...(phone.trim() ? { phone: phone.trim() } : {}) };
      return record<{ id: string }>("/api/vendors", { name, ...profileBody(draft), contact }, (made) => {
        added(made.id);
        return `${name.trim()} added.`;
      });
    }}>
      <Field label="Vendor's name" id="vendor-name" required maxLength={200} value={name}
        onChange={(e) => setName(e.target.value)} />
      <ProfileFields draft={draft} change={(part) => setDraft({ ...draft, ...part })} />
      <Field label="Email (optional)" id="vendor-email" type="email" maxLength={254} value={email}
        onChange={(e) => setEmail(e.target.value)} />
      <Field label="Phone (optional)" id="vendor-phone" maxLength={30} value={phone}
        onChange={(e) => setPhone(e.target.value)} />
      <div><Button type="submit">Add vendor</Button></div>
      <Notice tone="error">{error}</Notice>
    </Form>
  );
}
