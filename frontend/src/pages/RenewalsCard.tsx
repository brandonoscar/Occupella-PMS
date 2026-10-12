import { useEffect, useState } from "react";
import type { Me } from "../api";
import { ApiError, api } from "../api";
import { Button, LinkButton } from "../components/Button";
import { Notice } from "../components/Notice";
import { Card } from "../components/Shell";
import { when } from "../maintenance";
import { money } from "../money";
import type { Offer, SigningLink } from "../offers";
import { term } from "../offers";
import { leave } from "../router";

// The renewals the PMC has offered on a tenant's leases. The tenant reads the lease and signs it
// on the e-sign partner's page; the PMC makes it the lease once everyone on it has signed.
// Nothing shows when there is no renewal.
export function RenewalsCard({ me }: { me: Me }) {
  const [renewals, setRenewals] = useState<Offer[]>([]);
  const [error, setError] = useState("");

  function show(failed: unknown) {
    setError(failed instanceof ApiError ? failed.detail : String(failed));
  }

  useEffect(() => {
    api.get<Offer[]>("/api/tenant/renewals").then(setRenewals).catch(show);
  }, []);

  async function sign(offer: Offer) {
    setError("");
    try {
      const link = await api.post<SigningLink>(`/api/tenant/renewals/${offer.id}/signing-link`);
      leave(link.url);
    } catch (failed) {
      show(failed);
    }
  }

  if (renewals.length === 0 && !error) return null;
  return (
    <Card title="Lease renewal">
      <Notice tone="error">{error}</Notice>
      <ul className="flex flex-col divide-y divide-line">
        {renewals.map((offer) => {
          const mine = offer.signers.find((s) => s.email === me.email.toLowerCase());
          return (
            <li key={offer.id} className="flex flex-col gap-2 py-3" aria-label={`Renewal of ${offer.unit}`}>
              <p>
                {me.pmc_name} offers a renewal of {offer.property}, {offer.unit}:{" "}
                {term(offer.starts_on, offer.ends_on)}, {money(offer.monthly_rent)} a month.
              </p>
              <p className="text-sm text-ink-subtle">
                Signing: {offer.signers.map((s) => `${s.name}${s.signed_at ? " (signed)" : ""}`).join(", ")}
              </p>
              <div className="flex flex-wrap items-center gap-3">
                <LinkButton href={`/api/tenant/renewals/${encodeURIComponent(offer.id)}/document`}>
                  Read the lease (PDF)
                </LinkButton>
                {mine?.signed_at ? (
                  <span className="text-sm text-ink-muted" data-testid="renewal-signed">
                    You signed {when(mine.signed_at)}. It becomes your lease once everyone has signed.
                  </span>
                ) : mine ? (
                  <Button onClick={() => sign(offer)}>Sign</Button>
                ) : (
                  <span className="text-sm text-ink-muted">Someone else on the lease signs this one.</span>
                )}
              </div>
            </li>
          );
        })}
      </ul>
    </Card>
  );
}
