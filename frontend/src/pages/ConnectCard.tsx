import { useEffect, useState } from "react";
import { ApiError, api } from "../api";
import { Badge } from "../components/Badge";
import { Button } from "../components/Button";
import { AskFirst } from "../components/Confirm";
import { Notice } from "../components/Notice";
import { Said } from "../components/Said";
import { Select } from "../components/Select";
import { Card } from "../components/Shell";
import type { ConnectView, FeePayer } from "../connect";
import { CONNECT, cardFeeLine, connectLine, feesLine, requirementsLine } from "../connect";
import { usDate } from "../money";
import { leave } from "../router";
import { useRecord } from "./useRecord";

// Company setup: the company's own Stripe account, connected to Occupella's platform (docs/adr/
// 0014-stripe-connect.md). An admin connects it on Stripe's own onboarding pages, which return
// here; tenants and owners then pay by bank or card through it, Occupella's fee kept with
// Stripe's out of what reaches the trust account. Staff choose who pays the card fee, warned
// where a state's rule restricts it. Shown only on a server that offers Connect.
export function ConnectCard() {
  const [view, setView] = useState<ConnectView | null>(null);
  const [error, setError] = useState("");
  const [runs, setRuns] = useState(0);
  const { said, error: refused, record } = useRecord(() => setRuns((n) => n + 1));
  const show = (failed: unknown) => setError(failed instanceof ApiError ? failed.detail : String(failed));

  useEffect(() => {
    // Back from Stripe's onboarding: ask Stripe now, as its own report may not have come yet.
    const back = new URLSearchParams(window.location.search).get("connect") === "back";
    const asked = back && runs === 0
      ? api.post<ConnectView>(`${CONNECT}/refresh`, {}).catch(() => api.get<ConnectView>(CONNECT))
      : api.get<ConnectView>(CONNECT);
    asked.then(setView).catch(show);
  }, [runs]);

  async function connect() {
    setError("");
    try {
      const link = await api.post<{ url: string }>(`${CONNECT}/start`, {});
      leave(link.url);
    } catch (failed) {
      show(failed);
    }
  }

  if (view !== null && !view.offered) return null;
  return (
    <Card title="Stripe Connect">
      <Said>{said}</Said>
      <Notice tone="error">{error || refused}</Notice>
      {view === null ? (
        !error && <Notice tone="info">Loading</Notice>
      ) : (
        <>
          <div className="flex flex-wrap items-center gap-2">
            <p className="text-base text-ink" data-testid="connect-status">{connectLine(view)}</p>
            {view.connected && (
              <Badge tone={view.charges_enabled ? "positive" : "caution"}>
                {view.charges_enabled ? "Takes payments" : "Setup not finished"}
              </Badge>
            )}
            {view.connected && !view.live && <Badge tone="neutral">Test mode</Badge>}
          </div>
          {requirementsLine(view) && (
            <p className="mt-1 text-sm text-ink-muted" data-testid="connect-requirements">{requirementsLine(view)}</p>
          )}
          {view.connected && (
            <p className="mt-1 text-sm text-ink-muted">
              Payouts {view.payouts_enabled ? "on" : "not on yet"}: Stripe pays out to the bank account you gave it,
              which should be your operating trust account.
              {view.connected_at && ` Connected ${usDate(view.connected_at.slice(0, 10))} by ${view.connected_by}.`}
            </p>
          )}
          <p className="mt-2 text-sm text-ink-muted" data-testid="connect-fees">{feesLine(view)}</p>
          {view.may_change && (
            <div className="mt-3 flex flex-wrap gap-2">
              <Button onClick={connect}
                says="Opens Stripe's own pages, where you give Stripe what it asks. Nothing is paid.">
                {view.connected ? "Continue setup" : "Connect Stripe"}
              </Button>
              {view.connected && (
                <AskFirst variant="secondary" danger
                  question="Disconnect your Stripe account? Tenants and owners can't pay online through it, and autopays saved at it stop being charged. Payments already started still report in."
                  onConfirm={() => record(`${CONNECT}/disconnect`, {}, () => "Disconnected your Stripe account.")}>
                  Disconnect
                </AskFirst>
              )}
            </div>
          )}
          {!view.may_change && !view.connected && (
            <p className="mt-2 text-sm text-ink-muted">An admin connects the company's Stripe account.</p>
          )}
          <CardFees view={view} save={(payer) =>
            record(`${CONNECT}/card-fees`, { card_fee_payer: payer }, () => "Saved who pays the card fee.")} />
        </>
      )}
    </Card>
  );
}

function CardFees({ view, save }: { view: ConnectView; save: (payer: FeePayer) => unknown }) {
  const [payer, setPayer] = useState<FeePayer>(view.card_fee_payer);
  useEffect(() => setPayer(view.card_fee_payer), [view.card_fee_payer]);
  return (
    <div className="mt-4 flex flex-col gap-2">
      <p className="text-base" data-testid="card-fees">{cardFeeLine(view)}</p>
      <div className="flex flex-wrap items-end gap-2">
        <Select label="Who pays the card fee" id="card-fee-payer" value={payer}
          onChange={(e) => setPayer(e.target.value as FeePayer)}>
          <option value="company">The company, from its fees in trust</option>
          <option value="tenant">The tenant, by credit card only, where their state allows</option>
        </Select>
        <Button variant="secondary" disabled={payer === view.card_fee_payer} onClick={() => save(payer)}>
          Save card fee
        </Button>
      </div>
      <p className="text-sm text-ink-muted">
        Card networks cap a card fee at 3% and never allow one on a debit or prepaid card, so a tenant paying it pays
        by credit card. An owner's payment, and a card fee on autopay, are always the company's.
      </p>
      {view.rules.length > 0 && (
        <div className="text-sm text-ink-muted" data-testid="card-fee-rules">
          <p>State rules where your properties are, as researched (not legal advice: confirm them):</p>
          <ul className="mt-1 list-disc pl-5" aria-label="State rules on card fees">
            {view.rules.map((rule) => (
              <li key={rule.state}>
                {rule.state}: {rule.says}
                {rule.card === "banned" && ". Card fees there are the company's."}
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}
