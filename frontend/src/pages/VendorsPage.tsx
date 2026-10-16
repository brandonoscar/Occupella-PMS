import { Truck } from "lucide-react";
import { useEffect, useState } from "react";
import type { Me } from "../api";
import { ApiError, api } from "../api";
import { Badge } from "../components/Badge";
import { Button, LinkButton } from "../components/Button";
import { EmptyState } from "../components/EmptyState";
import { Field } from "../components/Field";
import { Notice } from "../components/Notice";
import { Select } from "../components/Select";
import { Card, PageHeader, Shell } from "../components/Shell";
import { day, money } from "../money";
import type { Vendor } from "../vendors";
import { COVERAGES, standingTone, vendorInsuranceHref } from "../vendors";
import { STAFF_NAV } from "./staffNav";
import { useRecord } from "./useRecord";

// Staff's vendors: each one's W-9 and insurance, how it stands today (current, running out
// within 30 days, expired), and recording a policy or its renewal. Vendors are added in Setup.
export function VendorsPage({ me }: { me: Me }) {
  const [vendors, setVendors] = useState<Vendor[] | null>(null);
  const [error, setError] = useState("");
  const today = day(new Date());

  function refresh() {
    api
      .get<Vendor[]>("/api/vendors")
      .then(setVendors)
      .catch((failed) => setError(failed instanceof ApiError ? failed.detail : String(failed)));
  }
  useEffect(refresh, []);

  return (
    <Shell me={me} nav={STAFF_NAV}>
      <PageHeader title="Vendors"
        actions={<LinkButton href={vendorInsuranceHref(today)}>Vendor insurance (PDF)</LinkButton>} />
      <Card title="Insurance">
        {vendors === null ? (
          <Notice tone={error ? "error" : "info"}>{error || "Loading"}</Notice>
        ) : vendors.length === 0 ? (
          <EmptyState icon={Truck} title="No vendors yet" body="Add a vendor in Setup, then record their insurance here." />
        ) : (
          <ul className="flex flex-col divide-y divide-line">
            {vendors.map((vendor) => (
              <li key={vendor.id} className="flex flex-col gap-2 py-4" aria-label={vendor.name}>
                <p className="flex items-center gap-3">
                  <span className="font-medium">{vendor.name}</span>
                  <Badge tone={vendor.w9_on_file ? "positive" : "caution"}>
                    {vendor.w9_on_file ? "W-9 on file" : "no W-9"}
                  </Badge>
                </p>
                {vendor.insurance.length === 0 ? (
                  <p className="text-sm text-ink-subtle">No insurance on file.</p>
                ) : (
                  <ul className="flex flex-col gap-1 text-sm">
                    {vendor.insurance.map((policy) => (
                      <li key={policy.coverage} className="flex flex-wrap items-center gap-2"
                        aria-label={`${vendor.name}, ${policy.coverage}`}>
                        <span className="first-letter:uppercase">{policy.coverage}</span>
                        <span className="text-ink-muted">
                          {policy.carrier} {policy.policy_number}, {policy.starts_on} to {policy.expires_on}
                          {policy.limit_amount ? `, limit ${money(policy.limit_amount)}` : ""}
                        </span>
                        <Badge tone={standingTone(policy.standing)} data-testid="standing">{policy.standing}</Badge>
                      </li>
                    ))}
                  </ul>
                )}
              </li>
            ))}
          </ul>
        )}
      </Card>
      {vendors && vendors.length > 0 && <PolicyCard vendors={vendors} refresh={refresh} />}
    </Shell>
  );
}

function PolicyCard({ vendors, refresh }: { vendors: Vendor[]; refresh: () => void }) {
  const { said, error, record } = useRecord(refresh);
  const [vendor, setVendor] = useState(vendors[0]?.id ?? "");
  const [coverage, setCoverage] = useState<string>(COVERAGES[0]);
  const [carrier, setCarrier] = useState("");
  const [number, setNumber] = useState("");
  const [starts, setStarts] = useState(day(new Date()));
  const [expires, setExpires] = useState("");
  const [limit, setLimit] = useState("");
  return (
    <Card title="Record a policy">
      <form
        className="flex flex-wrap items-end gap-3"
        onSubmit={async (event) => {
          event.preventDefault();
          const body = {
            coverage, carrier, policy_number: number, starts_on: starts, expires_on: expires,
            ...(limit.trim() ? { limit_amount: limit.trim() } : {}),
          };
          const name = vendors.find((v) => v.id === vendor)?.name ?? "the vendor";
          if (await record(`/api/vendors/${vendor}/insurance`, body, () => `${name}'s ${coverage} policy ${number} is recorded.`)) {
            setCarrier("");
            setNumber("");
            setExpires("");
            setLimit("");
          }
        }}
      >
        <Select label="Vendor" id="policy-vendor" value={vendor} onChange={(e) => setVendor(e.target.value)}>
          {vendors.map((v) => (
            <option key={v.id} value={v.id}>{v.name}</option>
          ))}
        </Select>
        <Select label="Coverage" id="policy-coverage" value={coverage} onChange={(e) => setCoverage(e.target.value)}>
          {COVERAGES.map((c) => (
            <option key={c} value={c}>{c}</option>
          ))}
        </Select>
        <Field label="Carrier" id="policy-carrier" required maxLength={200} value={carrier}
          onChange={(e) => setCarrier(e.target.value)} />
        <Field label="Policy number" id="policy-number" required maxLength={100} value={number}
          onChange={(e) => setNumber(e.target.value)} />
        <Field label="Starts" id="policy-starts" type="date" required value={starts}
          onChange={(e) => setStarts(e.target.value)} />
        <Field label="Expires" id="policy-expires" type="date" required value={expires}
          onChange={(e) => setExpires(e.target.value)} />
        <Field label="Limit (optional)" id="policy-limit" inputMode="decimal" value={limit}
          onChange={(e) => setLimit(e.target.value)} />
        <Button type="submit">Record policy</Button>
      </form>
      <Notice tone="info">{said}</Notice>
      <Notice tone="error">{error}</Notice>
    </Card>
  );
}
