import { useEffect, useState } from "react";
import { ApiError, api } from "../api";
import { Button } from "../components/Button";
import { Notice } from "../components/Notice";
import { Card, Wordmark } from "../components/Shell";
import type { ApplicantStatus, ApplicationFee } from "../leasing";
import { applicantStatus } from "../leasing";
import { when } from "../maintenance";
import { money } from "../money";

// Where an application stands: /application#<status token>, the link the applicant kept. Paying the
// fee leaves for the processor's page, which sends the applicant back to /application without the
// token (the processor never sees it), so the tab keeps it while they are away.
const KEPT = "pms.applicationStatus";

function tokenOf(): string {
  const hash = window.location.hash.slice(1);
  if (hash) return hash;
  try {
    const kept = sessionStorage.getItem(KEPT) ?? "";
    if (kept) window.history.replaceState(null, "", `/application${window.location.search}#${kept}`);
    return kept;
  } catch {
    return "";
  }
}

const PAY_WORDS: Record<ApplicationFee["methods"][number], string> = {
  card: "Pay by card",
  "bank debit": "Pay from a bank account",
};

// Paying its fee goes to the processor's page (go, which a test replaces).
export function ApplicationStatusPage({ go = (url: string) => window.location.assign(url) }: { go?: (url: string) => void }) {
  const [token] = useState(tokenOf);
  // Back from the processor's page, before it has said the money came.
  const back = new URLSearchParams(window.location.search).has("paid");
  const [status, setStatus] = useState<ApplicantStatus | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    api
      .post<ApplicantStatus>("/api/application/status", { status_token: token })
      .then(setStatus)
      .catch((failed) => setError(failed instanceof ApiError ? failed.detail : String(failed)));
  }, [token]);

  return (
    <div className="mx-auto flex max-w-2xl flex-col gap-6 px-6 py-10">
      <Wordmark />
      {status === null ? (
        <Notice tone={error ? "error" : "info"}>{error || "Loading"}</Notice>
      ) : (
        <Card title={`Your application for ${status.property}, ${status.unit}`}>
          <p className="text-sm text-ink-subtle">Sent {when(status.submitted_at)}</p>
          <p className="mt-3 text-md" data-testid="application-status">
            {applicantStatus(status.status)}
          </p>
          {status.conditions && <p className="mt-2">{status.conditions}</p>}
          {status.notice && (
            <div className="mt-4 border-t border-line pt-4">
              <h2 className="text-sm font-medium uppercase tracking-wide text-ink-muted">Adverse action notice</h2>
              <p className="mt-2 whitespace-pre-line text-base text-ink" data-testid="adverse-action-notice">{status.notice}</p>
            </div>
          )}
          {status.fee && (status.fee.due !== null || status.fee.paid !== null) && <FeePart fee={status.fee} token={token} back={back} go={go} />}
        </Card>
      )}
    </div>
  );
}

// The application fee (migration 0160): paid once, online, through the company's payment processor,
// whose page takes the card or bank account and sends the applicant back here.
function FeePart({ fee, token, back, go }: {
  fee: ApplicationFee;
  token: string;
  back: boolean;
  go: (url: string) => void;
}) {
  const [error, setError] = useState("");
  const [paying, setPaying] = useState<ApplicationFee["methods"][number] | null>(null);
  async function pay(method: ApplicationFee["methods"][number]) {
    setError("");
    setPaying(method);
    try {
      const { url } = await api.post<{ url: string }>("/api/application/fee", { status_token: token, method });
      try {
        sessionStorage.setItem(KEPT, token);
      } catch {
        // Without the tab's storage, the applicant comes back through the link they kept.
      }
      go(url);
    } catch (failed) {
      setError(failed instanceof ApiError ? failed.detail : String(failed));
      setPaying(null);
    }
  }
  return (
    <div className="mt-4 border-t border-line pt-4">
      <h2 className="text-sm font-medium uppercase tracking-wide text-ink-muted">Application fee</h2>
      {fee.paid !== null ? (
        <p className="mt-2 text-base" data-testid="application-fee">{`Paid ${money(fee.paid)}. Thank you.`}</p>
      ) : (
        <>
          <p className="mt-2 text-base" data-testid="application-fee">{`${money(fee.due ?? "0")} to pay`}</p>
          {back && (
            <Notice tone="info">Your payment is on its way. This page says it's paid once the payment processor tells us the money came.</Notice>
          )}
          {fee.methods.length === 0 ? (
            <p className="mt-1 text-sm text-ink-muted">The company doesn't take it online yet: ask them how to pay it.</p>
          ) : (
            <div className="mt-3 flex flex-wrap gap-2">
              {fee.methods.map((method) => (
                <Button key={method} busy={paying === method} disabled={paying !== null} onClick={() => pay(method)}>
                  {PAY_WORDS[method]}
                </Button>
              ))}
            </div>
          )}
        </>
      )}
      <Notice tone="error">{error}</Notice>
    </div>
  );
}
