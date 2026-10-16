import { useEffect, useState } from "react";
import type { Me } from "../api";
import { ApiError, api } from "../api";
import { Badge } from "../components/Badge";
import { ContactCard } from "../components/ContactCard";
import { Notice } from "../components/Notice";
import { RecordActions } from "../components/RecordActions";
import { Card, PageHeader, Shell } from "../components/Shell";
import { Wide } from "../components/Wide";
import { money, usDate } from "../money";
import { standingTone } from "../vendors";
import type { VendorPageData } from "../workOrders";
import { STATUS } from "../workOrders";
import { GoLink } from "./NeedsAttention";
import { FilesCard } from "./FilesCard";
import { STAFF_NAV } from "./staffNav";
import { Said } from "../components/Said";

const LINK = "font-medium text-iris hover:text-iris-hover hover:underline";

// A vendor (ADR 0005): their W-9 and insurance, contact details, the bills they sent and the work
// orders they were last assigned; renamed in place.
export function VendorPage({ me, id }: { me: Me; id: string }) {
  const [vendor, setVendor] = useState<VendorPageData | null>(null);
  const [said, setSaid] = useState("");
  const [error, setError] = useState("");

  function refresh() {
    api
      .get<VendorPageData>(`/api/vendors/${id}`)
      .then(setVendor)
      .catch((failed) => setError(failed instanceof ApiError ? failed.detail : String(failed)));
  }
  useEffect(refresh, [id]);

  return (
    <Shell me={me} nav={STAFF_NAV}>
      {vendor === null ? (
        <Notice tone={error ? "error" : "info"}>{error || "Loading"}</Notice>
      ) : (
        <>
          <PageHeader title={vendor.name} actions={
            <RecordActions path={`/api/vendors/${id}`} name={vendor.name}
              onChange={(done) => { setSaid(done); refresh(); }} />
          } />
          <p className="flex flex-wrap items-center gap-3 text-sm text-ink-muted">
            <GoLink href="/vendors" className={LINK}>Vendors</GoLink>
            <Badge tone={vendor.w9_on_file ? "positive" : "caution"}>{vendor.w9_on_file ? "W-9 on file" : "no W-9"}</Badge>
          </p>
          <Said>{said}</Said>
          <ContactCard path={`/api/vendors/${id}`} contact={vendor.contact} refresh={refresh} name={vendor.name} />
          <Card title="Insurance">
            {vendor.insurance.length === 0 ? (
              <p className="text-sm text-ink-subtle">No insurance on file. Record a policy on Vendors.</p>
            ) : (
              <ul className="flex flex-col gap-1 text-base" aria-label="Insurance">
                {vendor.insurance.map((policy) => (
                  <li key={policy.coverage} className="flex flex-wrap items-center gap-2">
                    <span className="first-letter:uppercase">{policy.coverage}</span>
                    <span className="text-ink-muted">
                      {policy.carrier} {policy.policy_number}, {usDate(policy.starts_on)} to {usDate(policy.expires_on)}
                    </span>
                    <Badge tone={standingTone(policy.standing)}>{policy.standing}</Badge>
                  </li>
                ))}
              </ul>
            )}
          </Card>
          <Card title="Bills">
            {vendor.bills.length === 0 ? (
              <p className="text-sm text-ink-subtle">No bills yet.</p>
            ) : (
              <Wide>
                <table className="w-full text-base">
                  <thead>
                    <tr>
                      <th className="text-left">Reference</th>
                      <th className="text-left">Owner and property</th>
                      <th className="text-left">Bill date</th>
                      <th className="text-right">Amount</th>
                      <th className="text-left">State</th>
                    </tr>
                  </thead>
                  <tbody>
                    {vendor.bills.map((bill) => (
                      <tr key={bill.id} aria-label={bill.reference}>
                        <td>{bill.reference}</td>
                        <td className="text-ink-muted">{bill.owner}, {bill.property}</td>
                        <td>{usDate(bill.bill_date)}</td>
                        <td className="text-right">{money(bill.amount)}</td>
                        <td><Badge tone={bill.paid ? "positive" : "caution"}>{bill.paid ? "Paid" : "Unpaid"}</Badge></td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </Wide>
            )}
          </Card>
          <Card title="Work orders">
            {vendor.work_orders.length === 0 ? (
              <p className="text-sm text-ink-subtle">No work order is assigned to them.</p>
            ) : (
              <ul className="flex flex-col gap-1 text-base" aria-label="Work orders">
                {vendor.work_orders.map((order) => (
                  <li key={order.id} className="flex flex-wrap items-center gap-2">
                    <GoLink href={`/work-orders/${order.id}`} className={LINK}>{order.summary}</GoLink>
                    <span className="text-ink-muted">{order.property}</span>
                    <Badge tone={STATUS[order.status].tone}>{STATUS[order.status].word}</Badge>
                  </li>
                ))}
              </ul>
            )}
          </Card>
          <FilesCard kind="w9" aboutId={id} name={vendor.name} />
        </>
      )}
    </Shell>
  );
}
