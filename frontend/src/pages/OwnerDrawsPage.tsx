import { Wallet } from "lucide-react";
import { useEffect, useState } from "react";
import type { Me } from "../api";
import { ApiError, api } from "../api";
import { EmptyState } from "../components/EmptyState";
import { Notice } from "../components/Notice";
import { Select } from "../components/Select";
import { Card, PageHeader, Shell } from "../components/Shell";
import { Wide } from "../components/Wide";
import { money } from "../money";
import type { OwnerMoney } from "../payables";
import { GoLink } from "./NeedsAttention";
import { DrawForm } from "./OwnersPage";
import { STAFF_NAV } from "./staffNav";
import { useRecord } from "./useRecord";
import { Said } from "../components/Said";

const LINK = "font-medium text-iris hover:text-iris-hover hover:underline";

// Accounting → Owner draws (ADR 0005): every owner's property account with what it holds, keeps
// back (the reserve and unpaid bills) and may pay out today, and a draw from one of them as the
// bank recorded it. The PMS sends no money itself.
export function OwnerDrawsPage({ me }: { me: Me }) {
  const [owners, setOwners] = useState<OwnerMoney[] | null>(null);
  const [error, setError] = useState("");
  const [chosen, setChosen] = useState("");

  function refresh() {
    api
      .get<OwnerMoney[]>("/api/payables/owners")
      .then(setOwners)
      .catch((why) => setError(why instanceof ApiError ? why.detail : String(why)));
  }
  useEffect(refresh, []);
  const owner = owners?.find((o) => o.account === chosen) ?? owners?.[0];

  return (
    <Shell me={me} nav={STAFF_NAV}>
      <PageHeader title="Owner draws" />
      {owners === null ? (
        <Notice tone={error ? "error" : "info"}>{error || "Loading"}</Notice>
      ) : owners.length === 0 || !owner ? (
        <EmptyState icon={Wallet} title="No owner has a property account yet."
          body="Open one from Settings, Company setup." />
      ) : (
        <>
          <Wide>
            <table className="w-full text-base">
              <thead>
                <tr>
                  <th className="text-left">Owner</th>
                  <th className="text-left">Property</th>
                  <th className="text-right">Holds</th>
                  <th className="text-right">Reserve</th>
                  <th className="text-right">Unpaid bills</th>
                  <th className="text-right">Available</th>
                </tr>
              </thead>
              <tbody>
                {owners.map((row) => (
                  <tr key={row.account} aria-label={`${row.owner}, ${row.property}`}>
                    <td><GoLink href={`/owners/${row.owner_id}`} className={LINK}>{row.owner}</GoLink></td>
                    <td><GoLink href={`/properties/${row.property_id}`} className={LINK}>{row.property}</GoLink></td>
                    <td className="text-right">{money(row.balance)}</td>
                    <td className="text-right">{money(row.reserve)}</td>
                    <td className="text-right">{money(row.unpaid_bills)}</td>
                    <td className="text-right font-medium">{money(row.available)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </Wide>
          <DrawCard owners={owners} owner={owner} choose={setChosen} refresh={refresh} />
        </>
      )}
    </Shell>
  );
}

function DrawCard({
  owners,
  owner,
  choose,
  refresh,
}: {
  owners: OwnerMoney[];
  owner: OwnerMoney;
  choose: (account: string) => void;
  refresh: () => void;
}) {
  const { said, error, record } = useRecord(refresh);
  return (
    <Card title="Record a draw">
      <div className="mb-3 max-w-md">
        <Select label="Owner and property" id="draw-account" value={owner.account}
          onChange={(event) => choose(event.target.value)}>
          {owners.map((o) => (
            <option key={o.account} value={o.account}>{o.owner}, {o.property}</option>
          ))}
        </Select>
      </div>
      <p className="mb-3 text-sm text-ink-muted" data-testid="draw-available">
        {money(owner.available)} available from {owner.bank_account}
      </p>
      <DrawForm key={owner.account} owner={owner} record={record} />
      <Said>{said}</Said>
      <Notice tone="error">{error}</Notice>
    </Card>
  );
}
