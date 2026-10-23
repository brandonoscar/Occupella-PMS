import { useEffect, useState } from "react";
import type { Me } from "../api";
import { ApiError, api } from "../api";
import { Button, LinkButton } from "../components/Button";
import { Field } from "../components/Field";
import { ListBar } from "../components/ListBar";
import { Notice } from "../components/Notice";
import { Select } from "../components/Select";
import { PageHeader, Shell } from "../components/Shell";
import { Wide } from "../components/Wide";
import type { Filters, Preset, Preview, ReportSpec } from "../reports";
import { PRESETS, columnLabel, defaultFilters, presetDays, reportArgs, reportHref, reportSpec } from "../reports";
import { useScope } from "../scope";
import type { Portfolio } from "../setup";
import { GoLink } from "./NeedsAttention";
import { BalanceNoticesPanel } from "./BalanceNoticesPanel";
import { STAFF_NAV } from "./staffNav";

const LINK = "font-medium text-iris hover:text-iris-hover hover:underline";
// Columns that are only for joining rows up, never for reading.
const HIDDEN = new Set(["line", "lease_id", "unit_id", "vendor_id", "staff_id", "bank_account_id"]);

// One report (ADR 0006, step 11): its filters (a day, or days with presets; an owner; a year;
// the property group or property, and an owner's properties, when its rows have one; q109), its
// rows on screen, and Export as PDF, Excel or CSV with the same filters.
export function ReportPage({ me, name }: { me: Me; name: string }) {
  const spec = reportSpec(name);
  if (!spec) {
    return (
      <Shell me={me} nav={STAFF_NAV}>
        <PageHeader title="Report" />
        <Notice tone="error">No report by that name.</Notice>
      </Shell>
    );
  }
  return <Report me={me} spec={spec} />;
}

function Report({ me, spec }: { me: Me; spec: ReportSpec }) {
  const [filters, setFilters] = useState<Filters>(() => defaultFilters(new Date()));
  const [scope, setScope] = useScope();
  const [owners, setOwners] = useState<{ id: string; name: string }[]>([]);
  // A report by property narrowed to one owner's properties; "" is all of them.
  const [byOwner, setByOwner] = useState("");
  const [preview, setPreview] = useState<Preview | null>(null);
  const [error, setError] = useState("");
  // Send balance notices, from the delinquency report (migration 20261023000201).
  const [notices, setNotices] = useState(false);
  const needsOwner = spec.args.includes("owner");
  const ranged = spec.args.includes("from") || spec.args.includes("periodStart");
  const args = reportArgs(spec, filters);
  const previewHref = args && reportHref(spec, args, scope, "json", byOwner);

  useEffect(() => {
    if (!needsOwner && !spec.byProperty) return;
    api
      .get<Portfolio>("/api/setup")
      .then((books) => {
        setOwners(books.owners);
        if (needsOwner) setFilters((now) => (now.owner ? now : { ...now, owner: books.owners[0]?.id ?? "" }));
      })
      .catch((failed) => setError(failed instanceof ApiError ? failed.detail : String(failed)));
  }, [needsOwner, spec.byProperty]);
  useEffect(() => {
    setPreview(null);
    setError("");
    if (!previewHref) return;
    // An answer for filters picked before these may come back after theirs: it is dropped.
    let current = true;
    api
      .get<Preview>(previewHref)
      .then((answer) => {
        if (current) setPreview(answer);
      })
      .catch((failed) => {
        if (current) setError(failed instanceof ApiError ? failed.detail : String(failed));
      });
    return () => {
      current = false;
    };
  }, [previewHref]);

  const change = (part: Partial<Filters>) => setFilters({ ...filters, ...part });
  const shown = preview ? preview.columns.map((c, n) => [c, n] as const).filter(([c]) => !HIDDEN.has(c)) : [];
  return (
    <Shell me={me} nav={STAFF_NAV}>
      <PageHeader title={spec.label} actions={args && (
        <div className="flex flex-wrap gap-2">
          {spec.name === "delinquency" && (
            <Button variant="secondary" onClick={() => setNotices(true)}>Send balance notices</Button>
          )}
          <div className="flex flex-wrap gap-2" aria-label="Export">
            <LinkButton href={reportHref(spec, args, scope, "pdf", byOwner)}>PDF</LinkButton>
            <LinkButton href={reportHref(spec, args, scope, "xlsx", byOwner)}>Excel</LinkButton>
            <LinkButton href={reportHref(spec, args, scope, "csv", byOwner)}>CSV</LinkButton>
          </div>
        </div>
      )} />
      {notices && <BalanceNoticesPanel scope={scope} onClose={() => setNotices(false)} />}
      <p className="text-base text-ink-muted"><GoLink href="/reports" className={LINK}>Reports</GoLink></p>
      <div className="flex flex-wrap items-end gap-3" aria-label="Filters">
        {spec.byProperty && <ListBar scope={scope} onScope={setScope} />}
        {spec.byProperty && (
          <Select label="Owner" id="report-by-owner" value={byOwner} onChange={(e) => setByOwner(e.target.value)}>
            <option value="">All owners</option>
            {owners.map((o) => <option key={o.id} value={o.id}>{o.name}</option>)}
          </Select>
        )}
        {needsOwner && (
          <Select label="Owner" id="report-owner" value={filters.owner} onChange={(e) => change({ owner: e.target.value })}>
            {owners.map((o) => <option key={o.id} value={o.id}>{o.name}</option>)}
          </Select>
        )}
        {spec.args.includes("asOf") && (
          <Field label="As of" id="report-as-of" type="date" value={filters.asOf} onChange={(e) => change({ asOf: e.target.value })} />
        )}
        {ranged && (
          <>
            <Select label="Days" id="report-preset" value="" onChange={(e) => {
              if (e.target.value) change(presetDays(e.target.value as Preset, new Date()));
            }}>
              <option value="">Choose…</option>
              {PRESETS.map((p) => <option key={p.value} value={p.value}>{p.label}</option>)}
            </Select>
            <Field label="From" id="report-from" type="date" value={filters.from} onChange={(e) => change({ from: e.target.value })} />
            <Field label="To" id="report-to" type="date" value={filters.to} onChange={(e) => change({ to: e.target.value })} />
          </>
        )}
        {spec.args.includes("year") && (
          <Field label="Year" id="report-year" inputMode="numeric" maxLength={4} value={filters.year}
            onChange={(e) => change({ year: e.target.value })} />
        )}
      </div>
      <Notice tone="error">{error}</Notice>
      {preview === null ? (
        !error && <Notice tone="info">{args ? "Loading" : "Choose what the report covers."}</Notice>
      ) : (
        <>
          <Wide>
            <table className="w-full text-sm" aria-label={spec.label}>
              <thead>
                <tr>{shown.map(([c]) => <th key={c} className="text-left">{columnLabel(c)}</th>)}</tr>
              </thead>
              <tbody>
                {preview.rows.map((row, r) => (
                  <tr key={r}>{shown.map(([c, n]) => <td key={c}>{row[n]}</td>)}</tr>
                ))}
              </tbody>
            </table>
          </Wide>
          {preview.total_rows > preview.rows.length && (
            <p className="text-sm text-ink-muted" data-testid="more">
              The first {preview.rows.length} of {preview.total_rows} lines; Export has them all.
            </p>
          )}
        </>
      )}
    </Shell>
  );
}
