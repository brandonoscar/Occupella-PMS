import { useEffect, useState } from "react";
import { ApiError, api } from "../api";
import { Notice } from "../components/Notice";
import { Pager } from "../components/Pager";
import { Card } from "../components/Shell";
import { Wide } from "../components/Wide";
import { money, usDate } from "../money";
import type { Paged } from "../rentals";

export type HistoryLine = {
  posted_on: string;
  type: string;
  description: string;
  amount: string;
  reference: string | null;
  unit: string | null;
  property: string | null;
};

// A tenant's, owner's or vendor's lines from the old system, before the switch (migration 0107,
// q129): brought in at Settings, Import, never posted, newest first a page at a time. Nothing to
// show, nothing shown.
export function HistoryCard({ about, id }: { about: "tenant" | "owner" | "vendor"; id: string }) {
  const [page, setPage] = useState<Paged<HistoryLine> | null>(null);
  const [n, setN] = useState(1);
  const [error, setError] = useState("");
  useEffect(() => {
    api.get<Paged<HistoryLine>>(`/api/history?${new URLSearchParams({ [`${about}_id`]: id, page: String(n) })}`)
      .then(setPage)
      .catch((failed) => setError(failed instanceof ApiError ? failed.detail : String(failed)));
  }, [about, id, n]);
  if (error) return <Card title="Before the switch"><Notice tone="error">{error}</Notice></Card>;
  if (page === null || page.total === 0) return null;
  const where = about === "tenant" ? "Unit" : about === "owner" ? "Property" : null;
  return (
    <Card title="Before the switch">
      <p className="mb-3 text-sm text-ink-muted">
        As the old system printed them. History only: none of it is in the books here.
      </p>
      <Wide>
        <table className="w-full text-base" aria-label="Before the switch">
          <thead>
            <tr>
              <th className="text-left">Date</th>
              {where && <th className="text-left">{where}</th>}
              <th className="text-left">Type</th>
              <th className="text-left">Description</th>
              <th className="text-left">Reference</th>
              <th className="text-right">Amount</th>
            </tr>
          </thead>
          <tbody>
            {page.items.map((line, i) => (
              <tr key={`${line.posted_on}-${i}`}>
                <td>{usDate(line.posted_on)}</td>
                {where && <td>{(about === "tenant" ? line.unit : line.property) ?? ""}</td>}
                <td>{line.type}</td>
                <td>{line.description}</td>
                <td>{line.reference ?? ""}</td>
                <td className="text-right">{money(line.amount)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </Wide>
      <div className="mt-3"><Pager page={page} onPage={setN} /></div>
    </Card>
  );
}
