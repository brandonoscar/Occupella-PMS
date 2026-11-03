import { useEffect, useState } from "react";
import { ApiError, api } from "../api";
import { Button } from "../components/Button";
import { Field } from "../components/Field";
import { Form } from "../components/Form";
import { Notice } from "../components/Notice";
import { Said } from "../components/Said";
import { Card } from "../components/Shell";
import { SidePanel } from "../components/SidePanel";
import { usDate } from "../money";
import { useRecord } from "./useRecord";

// The company's screening criteria (migration 0089; the founder's review, q085): the same for every
// applicant. The partner approves an income of at least approve_multiple times the rent, approves
// on conditions from conditional_multiple times, and declines less; the criteria in words show on
// the apply page. Until the company sets its own, the partner's (3 and 2 times) apply.
export type ScreeningCriteria = {
  id: string | null;
  approve_multiple: string;
  conditional_multiple: string;
  written: string | null;
  recorded_at: string | null;
  recorded_by: string | null;
};

export const SCREENING_CRITERIA = "/api/leasing/screening-criteria";

// "Approved at 3 times the rent; on conditions at 2 times; declined below": a multiple as typed.
export function criteriaLine(criteria: ScreeningCriteria): string {
  const times = (m: string) => `${Number(m)} times the rent`;
  return `Approved at ${times(criteria.approve_multiple)} or more; on conditions at ${times(criteria.conditional_multiple)}; declined below`;
}

export function ScreeningCriteriaCard() {
  const [criteria, setCriteria] = useState<ScreeningCriteria | null>(null);
  const [error, setError] = useState("");
  const [editing, setEditing] = useState(false);
  const [runs, setRuns] = useState(0);
  const { said, error: refused, record } = useRecord(() => setRuns((n) => n + 1));
  useEffect(() => {
    api.get<ScreeningCriteria>(SCREENING_CRITERIA).then(setCriteria)
      .catch((failed) => setError(failed instanceof ApiError ? failed.detail : String(failed)));
  }, [runs]);

  return (
    <Card title="Screening criteria">
      <Said>{said}</Said>
      {criteria === null ? (
        <Notice tone={error ? "error" : "info"}>{error || "Loading"}</Notice>
      ) : (
        <>
          <p className="text-base" data-testid="screening-criteria">{criteriaLine(criteria)}</p>
          {criteria.written && <p className="mt-2 whitespace-pre-line text-base text-ink-secondary">{criteria.written}</p>}
          <p className="mt-1 text-sm text-ink-muted">
            {criteria.id === null
              ? "The screening partner's own, until the company sets its criteria."
              : `Set ${usDate(criteria.recorded_at)}${criteria.recorded_by ? ` by ${criteria.recorded_by}` : ""}. Every applicant is screened by them, and the apply page shows them.`}
          </p>
          <div className="mt-3">
            <Button variant="secondary" onClick={() => setEditing(true)}>Edit screening criteria</Button>
          </div>
          {editing && (
            <SidePanel title="Screening criteria" onClose={() => setEditing(false)}>
              <CriteriaForm criteria={criteria} save={(body) =>
                record(SCREENING_CRITERIA, body, () => "Saved the screening criteria.").then((ok) => {
                  if (ok) setEditing(false);
                })} />
              <Notice tone="error">{refused}</Notice>
            </SidePanel>
          )}
        </>
      )}
    </Card>
  );
}

function CriteriaForm({ criteria, save }: { criteria: ScreeningCriteria; save: (body: unknown) => Promise<void> }) {
  const [approve, setApprove] = useState(criteria.approve_multiple);
  const [conditional, setConditional] = useState(criteria.conditional_multiple);
  const [written, setWritten] = useState(criteria.written ?? "");
  return (
    <Form className="flex flex-col gap-3" onSubmit={(event) => {
      event.preventDefault();
      void save({ approve_multiple: approve, conditional_multiple: conditional, written: written.trim() || null });
    }}>
      <Field label="Approve from (times the rent)" id="criteria-approve" inputMode="decimal" required value={approve}
        onChange={(e) => setApprove(e.target.value)} />
      <Field label="Approve on conditions from (times the rent)" id="criteria-conditional" inputMode="decimal" required
        value={conditional} onChange={(e) => setConditional(e.target.value)} />
      <label htmlFor="criteria-written" className="flex flex-col gap-1.5 text-sm text-ink-muted">
        In words, for applicants (optional)
        <textarea id="criteria-written" rows={4} maxLength={4000} value={written} onChange={(e) => setWritten(e.target.value)}
          className="rounded-sm border border-line-strong bg-sunken px-3 py-2 text-base text-ink" />
      </label>
      <div><Button type="submit"
        says="Screenings from now on use these, and the apply page shows the words. Earlier screenings stay as they were.">Save</Button></div>
    </Form>
  );
}
