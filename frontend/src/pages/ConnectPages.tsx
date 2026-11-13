import { useEffect, useState } from "react";
import { ApiError, api } from "../api";
import { Badge } from "../components/Badge";
import { Button, LinkButton } from "../components/Button";
import { Notice } from "../components/Notice";
import { Card, PageHeader } from "../components/Shell";
import type { Method } from "../connect";
import { CONNECT, METHOD_NAMES } from "../connect";
import { money } from "../money";
import type { OnlineStatus } from "../payments";
import { STATUS } from "../payments";

// The fake Connect platform's pages (development and tests only, PMS_CONNECT=fake): onboarding,
// a payment by bank or card with what the bank, the card network or a dispute did, and saving a
// bank account or card for autopay. Each says what Stripe's would, and moves no money.

type FakeAccount = { company: string; charges_enabled: boolean };
type FakePayment = {
  method: Method;
  amount: string;
  tenant_fee: string;
  total: string;
  platform_fee: string;
  memo: string;
  status: OnlineStatus;
  dispute: "opened" | "won" | "lost" | null;
  done: string;
};
type FakeSave = { tenant: string; memo: string; method: Method; saved: boolean };
type Outcome = "paid" | "failed" | "expired" | "disputed" | "won" | "lost";

const TITLE = "Test Stripe Connect";
const DESCRIPTION = "For development and tests only: no money moves.";
const DISPUTE: Record<"opened" | "won" | "lost", string> = {
  opened: "Disputed",
  won: "Dispute won",
  lost: "Dispute lost",
};

function useFake<T>(path: string) {
  const [page, setPage] = useState<T | null>(null);
  const [error, setError] = useState("");
  const [runs, setRuns] = useState(0);
  const show = (failed: unknown) => setError(failed instanceof ApiError ? failed.detail : String(failed));
  useEffect(() => {
    api.get<T>(path).then(setPage).catch(show);
  }, [path, runs]);
  async function send(body: unknown) {
    setError("");
    try {
      await api.post(path, body);
      setRuns((n) => n + 1);
    } catch (failed) {
      show(failed);
    }
  }
  return { page, error, send };
}

// Stripe's onboarding, as the fake platform plays it: finished, or left wanting something.
export function ConnectOnboardingPage() {
  const account = window.location.hash.slice(1);
  const { page, error, send } = useFake<FakeAccount>(
    `${CONNECT}/fake/accounts/${encodeURIComponent(account)}`,
  );
  return (
    <main className="mx-auto flex max-w-xl flex-col gap-4 p-6">
      <PageHeader title={TITLE} description={DESCRIPTION} />
      <Notice tone="error">{error}</Notice>
      {page !== null && (
        <Card title={`Onboarding for ${page.company}`}>
          <p className="mb-4 text-base" data-testid="fake-onboarding">
            {page.charges_enabled
              ? "Finished: the account takes payments and pays out."
              : "Stripe asks for the business's details and the bank account it pays out to."}
          </p>
          <div className="flex flex-wrap gap-2">
            <Button onClick={() => send({ outcome: "finished" })}>Finish onboarding</Button>
            <Button variant="secondary" onClick={() => send({ outcome: "wanting" })}>Leave the bank account for later</Button>
            <LinkButton href="/settings/setup?connect=back">Back to Company setup</LinkButton>
          </div>
        </Card>
      )}
    </main>
  );
}

// A payment through the fake platform: what is paid, by what, Occupella's fee in it, and buttons
// that say what happened next.
export function ConnectPayPage() {
  const payment = window.location.hash.slice(1);
  const { page, error, send } = useFake<FakePayment>(
    `${CONNECT}/fake/payments/${encodeURIComponent(payment)}`,
  );
  const says = (outcome: Outcome) => send({ outcome });
  const card = page?.method === "card";
  return (
    <main className="mx-auto flex max-w-xl flex-col gap-4 p-6">
      <PageHeader title={TITLE} description={DESCRIPTION} />
      <Notice tone="error">{error}</Notice>
      {page !== null && (
        <Card title={page.memo}>
          <p className="mb-2 text-base" data-testid="fake-total">
            {money(page.total)} by {METHOD_NAMES[page.method].toLowerCase()}
            {page.tenant_fee !== "0.00" && ` (${money(page.amount)} + ${money(page.tenant_fee)} fee)`}
          </p>
          <p className="mb-2 text-sm text-ink-muted" data-testid="fake-platform-fee">
            Occupella's fee in it: {money(page.platform_fee)}
          </p>
          <p className="mb-4 flex flex-wrap gap-2">
            <Badge tone={STATUS[page.status].tone}>{STATUS[page.status].label}</Badge>
            {page.dispute && (
              <Badge tone={page.dispute === "won" ? "positive" : "danger"}>{DISPUTE[page.dispute]}</Badge>
            )}
          </p>
          <div className="flex flex-wrap gap-2">
            {page.status === "started" && (
              <>
                <Button onClick={() => says("paid")}>{card ? "The card paid it" : "The bank paid it"}</Button>
                <Button variant="secondary" onClick={() => says("failed")}>It didn't go through</Button>
                <Button variant="secondary" onClick={() => says("expired")}>Left without paying</Button>
              </>
            )}
            {page.status === "paid" && page.dispute === null && (
              <Button variant="secondary" onClick={() => says("disputed")}>
                {card ? "The cardholder disputed it" : "The bank returned it"}
              </Button>
            )}
            {page.dispute === "opened" && (
              <>
                <Button variant="secondary" onClick={() => says("won")}>The company won the dispute</Button>
                <Button variant="secondary" onClick={() => says("lost")}>The company lost the dispute</Button>
              </>
            )}
            <LinkButton href={`${page.done}?paid=1`}>Back to the portal</LinkButton>
          </div>
        </Card>
      )}
    </main>
  );
}

// Saving a bank account or card for autopay on the fake platform: a made-up one.
export function ConnectSavePage() {
  const autopay = window.location.hash.slice(1);
  const { page, error, send } = useFake<FakeSave>(`${CONNECT}/fake/autopay/${encodeURIComponent(autopay)}`);
  const card = page?.method === "card";
  return (
    <main className="mx-auto flex max-w-xl flex-col gap-4 p-6">
      <PageHeader title={TITLE} description={DESCRIPTION} />
      <Notice tone="error">{error}</Notice>
      {page !== null && (
        <Card title={`Autopay for ${page.memo}`}>
          <p className="mb-4 text-base" data-testid="fake-save">
            {page.saved
              ? `Saved: ${card ? "Test card ••••4242" : "Test bank ••••0000"}.`
              : `${page.tenant} saves ${card ? "a card" : "a bank account"} to pay from.`}
          </p>
          <div className="flex flex-wrap gap-2">
            {!page.saved && <Button onClick={() => send({})}>{card ? "Save this card" : "Save this bank account"}</Button>}
            <LinkButton href="/pay/done?autopay=1">Back to the portal</LinkButton>
          </div>
        </Card>
      )}
    </main>
  );
}
