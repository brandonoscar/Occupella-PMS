import { useEffect, useState } from "react";
import { ApiError, api } from "../api";
import { LinkButton } from "../components/Button";
import { Notice } from "../components/Notice";
import { Card, Wordmark } from "../components/Shell";
import type { Vacancies } from "../leasing";
import { money } from "../money";

// A PMC's public vacancy page: /listings?pmc=<PMC id>, the units its staff listed, each with a
// way to apply. No login, and nothing here is sent to listing sites.
export function ListingsPage() {
  const [pmc] = useState(() => new URLSearchParams(window.location.search).get("pmc") ?? "");
  const [page, setPage] = useState<Vacancies | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    if (!pmc) {
      setError("This page needs the property manager's link.");
      return;
    }
    api
      .get<Vacancies>(`/api/listings/${encodeURIComponent(pmc)}`)
      .then(setPage)
      .catch((failed) => setError(failed instanceof ApiError ? failed.detail : String(failed)));
  }, [pmc]);

  return (
    <div className="mx-auto flex max-w-2xl flex-col gap-6 px-6 py-10">
      <Wordmark />
      {page === null ? (
        <Notice tone={error ? "error" : "info"}>{error || "Loading"}</Notice>
      ) : (
        <Card title={`Homes for rent from ${page.pmc}`}>
          {page.listings.length === 0 ? (
            <Notice tone="info">Nothing is available right now.</Notice>
          ) : (
            <ul className="flex flex-col divide-y divide-line">
              {page.listings.map((listing) => (
                <li key={listing.id} className="flex flex-col gap-2 py-4" aria-label={`${listing.property}, ${listing.unit}`}>
                  <p className="text-md font-medium">
                    {listing.property}, {listing.unit}
                  </p>
                  <p className="text-ink-muted">{money(listing.monthly_rent)} a month</p>
                  {listing.description && <p className="whitespace-pre-line">{listing.description}</p>}
                  <div>
                    <LinkButton href={`/apply?listing=${encodeURIComponent(listing.id)}`}>Apply</LinkButton>
                  </div>
                </li>
              ))}
            </ul>
          )}
        </Card>
      )}
    </div>
  );
}
