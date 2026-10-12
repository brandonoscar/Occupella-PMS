import { useEffect, useState } from "react";
import { ApiError, api } from "../api";
import { Notice } from "../components/Notice";
import { Card, Wordmark } from "../components/Shell";
import type { ApplicantStatus } from "../leasing";
import { applicantStatus } from "../leasing";
import { when } from "../maintenance";

// Where an application stands: /application#<status token>, the link the applicant kept.
export function ApplicationStatusPage() {
  const token = window.location.hash.slice(1);
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
        </Card>
      )}
    </div>
  );
}
