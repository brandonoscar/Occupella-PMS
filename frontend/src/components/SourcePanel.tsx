import { useEffect, useState } from "react";
import { ApiError, api } from "../api";
import { latest } from "../latest";
import { Notice } from "./Notice";
import { SidePanel } from "./SidePanel";
import { withBase } from "../base";

// Where a line of a statement or a ledger came from, as the API says it
// (src/pms/api/ledger_sources.py): its rows in words and the files to download.
export type Source = {
  kind: string;
  title: string;
  rows: { label: string; value: string }[];
  links: { label: string; url: string }[];
};

// A line's source in a side panel (the founder's review of 11/03, item 8): a tenant's payment and
// its receipt, a bill and its receipts, how a management fee was worked out, how a payout left.
export function SourcePanel({ url, onClose }: { url: string; onClose: () => void }) {
  const [source, setSource] = useState<Source | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    const run = latest();
    setSource(null);
    setError("");
    api
      .get<Source>(url)
      .then(run.keep(setSource))
      .catch(run.keep((failed: unknown) => setError(failed instanceof ApiError ? failed.detail : String(failed))));
    return run.stop;
  }, [url]);

  return (
    <SidePanel title={source?.title ?? "Details"} onClose={onClose}>
      <Notice tone="error">{error}</Notice>
      {source === null ? (
        !error && <Notice tone="info">Loading</Notice>
      ) : (
        <>
          <dl className="flex flex-col gap-3 text-base">
            {source.rows.map((row, n) => (
              <div key={n} className="flex flex-col gap-0.5">
                <dt className="text-sm text-ink-muted">{row.label}</dt>
                <dd className="text-ink">{row.value}</dd>
              </div>
            ))}
          </dl>
          {source.links.length > 0 && (
            <ul className="flex flex-col gap-2 border-t border-line pt-4">
              {source.links.map((link) => (
                <li key={link.url}>
                  <a className="text-iris hover:text-iris-hover hover:underline" href={withBase(link.url)} download>
                    {link.label}
                  </a>
                </li>
              ))}
            </ul>
          )}
        </>
      )}
    </SidePanel>
  );
}

// A line's description as a button that opens its source, or as plain text without one.
export function OpensSource({
  onOpen,
  children,
}: {
  onOpen: (() => void) | null;
  children: React.ReactNode;
}) {
  if (!onOpen) return <>{children}</>;
  return (
    <button type="button" aria-haspopup="dialog" onClick={onOpen}
      className="text-left text-ink hover:text-iris hover:underline">
      {children}
    </button>
  );
}
