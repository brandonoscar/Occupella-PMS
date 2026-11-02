import { useEffect, useState } from "react";
import { ApiError, api } from "../api";
import { Button } from "../components/Button";
import { Field } from "../components/Field";
import { Form } from "../components/Form";
import { Notice } from "../components/Notice";
import { Said } from "../components/Said";
import { Card } from "../components/Shell";
import { SidePanel } from "../components/SidePanel";
import { Wide } from "../components/Wide";
import { latest } from "../latest";
import { money, usDate } from "../money";
import type { BankSettings, PayoutRun } from "../payouts";
import { methodWord, runFile } from "../payouts";
import { useRecord } from "./useRecord";

const LINK = "font-medium text-iris hover:text-iris-hover hover:underline";

// The payout files Pay owners and Pay bills made (ADR 0010 phase 1b): each run's ACH file to
// upload to the bank, or its checks to print on the account's check stock, newest first. A file
// downloads again as often as needed; each download is logged, and only staff who may pay out
// money (the disburse role) take one.
export function PayoutFiles({ query, runs = 0, title = "Payout files" }: { query: Record<string, string>; runs?: number; title?: string }) {
  const [found, setFound] = useState<PayoutRun[] | null>(null);
  const [error, setError] = useState("");
  const search = new URLSearchParams(query).toString();
  useEffect(() => {
    const run = latest();
    api.get<PayoutRun[]>(`/api/payouts/runs${search ? `?${search}` : ""}`)
      .then(run.keep(setFound))
      .catch(run.keep((why) => setError(why instanceof ApiError ? why.detail : String(why))));
    return run.stop;
  }, [search, runs]);

  return (
    <Card title={title}>
      {found === null ? (
        <Notice tone={error ? "error" : "info"}>{error || "Loading"}</Notice>
      ) : found.length === 0 ? (
        <p className="text-sm text-ink-subtle">No payout file made yet. Pick a payout file on Pay owners or Pay bills to make one.</p>
      ) : (
        <Wide>
          <table className="w-full text-base" aria-label={title}>
            <thead>
              <tr>
                <th className="text-left">Paid</th>
                <th className="text-left">For</th>
                <th className="text-left">By</th>
                <th className="text-left">From</th>
                <th className="text-right">Payouts</th>
                <th className="text-right">Total</th>
                <th className="text-left">Recorded by</th>
                <th><span className="sr-only">File</span></th>
              </tr>
            </thead>
            <tbody>
              {found.map((run) => {
                const file = runFile(run);
                return (
                  <tr key={run.id}>
                    <td className="whitespace-nowrap">{usDate(run.paid_on)}</td>
                    <td>{run.kind === "owners" ? "Owners" : "Bills"}</td>
                    <td>{methodWord(run.method)}</td>
                    <td>{run.bank_account}</td>
                    <td className="text-right">{run.payouts}</td>
                    <td className="text-right">{money(run.total)}</td>
                    <td className="text-ink-muted">{run.recorded_by ?? "The PMS"}</td>
                    <td className="text-right whitespace-nowrap">
                      <a href={file.href} className={LINK}>{file.label}</a>
                      {run.downloads > 0 && (
                        <span className="block text-sm text-ink-muted">
                          Taken {run.downloads} time{run.downloads === 1 ? "" : "s"}
                        </span>
                      )}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </Wide>
      )}
    </Card>
  );
}

// "Synthetic Bank, routing 001234561; origin 1000000000 Golden PMC; company 1000000000 Golden":
// what an account's bank gave the company for its ACH files.
export function achLine(settings: BankSettings): string {
  if (!settings.ach_ready) return "Not set: ACH files can't be made from this account yet.";
  return `${settings.bank_name}, routing ${settings.bank_routing_number}; origin ${settings.origin_id} `
    + `${settings.origin_name}; company ${settings.company_id} ${settings.company_name}`;
}

// A trust bank account's Payouts tab: the ACH details its bank gave the company, the number its
// printed checks start from, and the files made from it.
export function PayoutsTab({ bankId }: { bankId: string }) {
  const path = `/api/payouts/bank-accounts/${bankId}`;
  const [settings, setSettings] = useState<BankSettings | null>(null);
  const [error, setError] = useState("");
  const [editing, setEditing] = useState(false);
  const [runs, setRuns] = useState(0);
  const { said, error: refused, record } = useRecord(() => setRuns((n) => n + 1));
  useEffect(() => {
    const run = latest();
    api.get<BankSettings>(path).then(run.keep(setSettings))
      .catch(run.keep((why) => setError(why instanceof ApiError ? why.detail : String(why))));
    return run.stop;
  }, [path, runs]);

  return (
    <div className="flex flex-col gap-6">
      <Said>{said}</Said>
      <Card title="ACH and checks">
        {settings === null ? (
          <Notice tone={error ? "error" : "info"}>{error || "Loading"}</Notice>
        ) : (
          <>
            <dl className="grid grid-cols-[max-content_1fr] gap-x-6 gap-y-1 text-base" aria-label="ACH and checks">
              <dt className="text-ink-muted">ACH files</dt>
              <dd data-testid="ach-details">{achLine(settings)}</dd>
              <dt className="text-ink-muted">Next check</dt>
              <dd data-testid="next-check">Number {settings.next_check_number}</dd>
            </dl>
            <p className="mt-2 text-sm text-ink-muted">
              The PMS writes the ACH file for the bank and prints checks on the account's check stock; the bank
              moves the money. Nothing here sends a payment.
            </p>
            <div className="mt-3"><Button variant="secondary" onClick={() => setEditing(true)}>Edit ACH and checks</Button></div>
            {editing && (
              <SidePanel title="ACH and checks" onClose={() => setEditing(false)}>
                <SettingsForm settings={settings} save={(body) =>
                  record(path, body, () => "Saved the account's ACH details and checks.").then((ok) => {
                    if (ok) setEditing(false);
                  })} />
                <Notice tone="error">{refused}</Notice>
              </SidePanel>
            )}
          </>
        )}
      </Card>
      <PayoutFiles query={{ bank_account_id: bankId }} runs={runs} />
    </div>
  );
}

const ACH_FIELDS = [
  ["bank_routing_number", "Bank's routing number", "9 digits"],
  ["bank_name", "Bank's name", "As the bank writes it, up to 23 characters"],
  ["origin_id", "Origin ID", "10 characters, as the bank gives it"],
  ["origin_name", "Origin name", "Up to 23 characters"],
  ["company_id", "Company ID", "10 characters, as the bank gives it"],
  ["company_name", "Company name", "Up to 16 characters"],
] as const;

type Draft = Record<(typeof ACH_FIELDS)[number][0], string> & { first_check_number: string };

function SettingsForm({ settings, save }: { settings: BankSettings; save: (body: unknown) => Promise<void> }) {
  const [draft, setDraft] = useState<Draft>(() => ({
    ...(Object.fromEntries(ACH_FIELDS.map(([key]) => [key, settings[key] ?? ""])) as Record<(typeof ACH_FIELDS)[number][0], string>),
    first_check_number: settings.first_check_number === null ? "" : String(settings.first_check_number),
  }));
  return (
    <Form className="flex flex-col gap-3" onSubmit={(event) => {
      event.preventDefault();
      return save({
        ...Object.fromEntries(ACH_FIELDS.map(([key]) => [key, draft[key] === "" ? null : draft[key]])),
        first_check_number: draft.first_check_number === "" ? null : Number(draft.first_check_number),
      });
    }}>
      <p className="text-sm text-ink-muted">What the bank gave the company for ACH files: all of them, or none.</p>
      {ACH_FIELDS.map(([key, label, hint]) => (
        <Field key={key} label={label} id={`ach-${key}`} placeholder={hint} value={draft[key]}
          maxLength={key === "bank_routing_number" ? 9 : key.endsWith("_id") ? 10 : key === "company_name" ? 16 : 23}
          onChange={(e) => setDraft((was) => ({ ...was, [key]: e.target.value }))} />
      ))}
      <Field label="First check number" id="ach-first-check" inputMode="numeric" pattern="[0-9]*"
        value={draft.first_check_number}
        onChange={(e) => setDraft((was) => ({ ...was, first_check_number: e.target.value }))} />
      <div>
        <Button type="submit" says="ACH files and checks from this account use these from now on; the old details stay in the history. Nothing is sent.">
          Save
        </Button>
      </div>
    </Form>
  );
}
