import { House } from "lucide-react";
import { useEffect, useState } from "react";
import { ApiError, api } from "../api";
import { LinkButton } from "../components/Button";
import { EmptyState } from "../components/EmptyState";
import { Notice } from "../components/Notice";
import { PageHeader, Wordmark } from "../components/Shell";
import type { Vacancies } from "../leasing";
import { money } from "../money";
import { sizeLine } from "../unitDetails";

// A PMC's public vacancy page: /listings?pmc=<PMC id>, the units its staff listed, each with a
// way to apply. No login. The listings staff send to listing sites are in the company's feeds
// too (Settings, Listing feeds).
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
    <div className="mx-auto flex max-w-2xl flex-col gap-8 px-6 py-10">
      <Wordmark />
      {page === null ? (
        <Notice tone={error ? "error" : "info"}>{error || "Loading"}</Notice>
      ) : (
        <>
          <PageHeader
            title={`Homes for rent from ${page.pmc}`}
            description="Each home's rent and a way to apply."
          />
          {page.listings.length === 0 ? (
            <EmptyState
              icon={House}
              title="Nothing is available right now."
              body={`Homes ${page.pmc} lists show here as soon as they are listed.`}
            />
          ) : (
            <ul className="flex flex-col divide-y divide-line border-y border-line">
              {page.listings.map((listing) => (
                <li key={listing.id} className="flex flex-col gap-2 py-4" aria-label={`${listing.property}, ${listing.unit}`}>
                  <div className="flex flex-wrap items-baseline justify-between gap-2">
                    <p className="text-md font-medium">
                      {listing.property}, {listing.unit}
                    </p>
                    <p className="text-ink-muted">{money(listing.monthly_rent)} a month</p>
                  </div>
                  {sizeLine(listing) && <p className="text-ink-secondary">{sizeLine(listing)}</p>}
                  {listing.photos.length > 0 && (
                    <ul className="flex gap-2 overflow-x-auto" aria-label={`Photos, ${listing.property}, ${listing.unit}`}>
                      {listing.photos.map((photo, n) => (
                        <li key={photo} className="shrink-0">
                          <img src={`/api/listings/${encodeURIComponent(pmc)}/photos/${photo}`}
                            alt={`${listing.property}, ${listing.unit}, ${n + 1} of ${listing.photos.length}`} loading="lazy"
                            className="h-32 w-48 rounded-sm object-cover" />
                        </li>
                      ))}
                    </ul>
                  )}
                  {listing.description && (
                    <p className="whitespace-pre-line text-ink-secondary">{listing.description}</p>
                  )}
                  <div>
                    <LinkButton href={`/apply?listing=${encodeURIComponent(listing.id)}`}>Apply</LinkButton>
                  </div>
                </li>
              ))}
            </ul>
          )}
        </>
      )}
    </div>
  );
}
