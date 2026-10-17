import { useEffect, useState } from "react";
import type { Me } from "../api";
import { ApiError, api } from "../api";
import { Badge } from "../components/Badge";
import { Button, LinkButton } from "../components/Button";
import { EmailsCard } from "../components/EmailsCard";
import { Field } from "../components/Field";
import { Form } from "../components/Form";
import { Notice } from "../components/Notice";
import type { RowAction } from "../components/RowMenu";
import { RowMenu } from "../components/RowMenu";
import { Said } from "../components/Said";
import { Select } from "../components/Select";
import { Card, PageHeader, Shell } from "../components/Shell";
import { SidePanel } from "../components/SidePanel";
import type { Application, Decision } from "../leasing";
import { applicantNames, applicationBadge, incomeMultiple, needsNotice, recommendationLabel } from "../leasing";
import { when } from "../maintenance";
import { day, money, usDate } from "../money";
import { GoLink } from "./NeedsAttention";
import { STAFF_NAV } from "./staffNav";
import type { Recorder } from "./useRecord";
import { useRecord } from "./useRecord";

const LINK = "font-medium text-iris hover:text-iris-hover hover:underline";
const TEXTAREA = "rounded-sm border border-line-strong bg-sunken px-3 py-2 text-base text-ink";

type Step = "approve" | "reject" | "withdraw";
const TITLES: Record<Step, string> = { approve: "Approve", reject: "Reject", withdraw: "Record withdrawal" };
const DECIDED: Record<Decision["decision"], string> = {
  approved: "Approved",
  conditional: "Approved on conditions",
  declined: "Rejected",
};

// An application's own page (ADR 0006): who applied for which unit, their household income
// against the rent, each screening the partner reported, and the decision with its adverse action
// notice. Staff screen it, approve or reject it beside its title, and make an approved one a draft
// lease; its ⋯ menu records that the applicants withdrew it. The PMS keeps names, emails and
// income only; the screening partner keeps the rest (ADR 0002).
export function ApplicantPage({ me, id }: { me: Me; id: string }) {
  const [found, setFound] = useState<Application | null>(null);
  const [error, setError] = useState("");
  const [step, setStep] = useState<Step | null>(null);
  const [runs, setRuns] = useState(0);
  const { said, error: refused, record } = useRecord(() => setRuns((n) => n + 1));

  useEffect(() => {
    api.get<Application>(`/api/leasing/applications/${id}`).then(setFound)
      .catch((why) => setError(why instanceof ApiError ? why.detail : String(why)));
  }, [id, runs]);

  const recordThenClose: Recorder = async (path, body, done) => {
    const recorded = await record(path, body, done);
    if (recorded) setStep(null);
    return recorded;
  };

  if (found === null) {
    return (
      <Shell me={me} nav={STAFF_NAV}>
        <PageHeader title="Applicant" />
        <Notice tone={error ? "error" : "info"}>{error || "Loading"}</Notice>
      </Shell>
    );
  }
  const names = applicantNames(found);
  const badge = applicationBadge(found.status);
  const deciding = found.status === "new" || found.status === "screening";
  const approved = found.status === "approved" || found.status === "conditional";
  const menu: RowAction[] = found.status !== "rejected" && found.status !== "withdrawn" && found.lease_id === null
    ? [{ label: "Record withdrawal", danger: true, onSelect: () => setStep("withdraw") }]
    : [];
  return (
    <Shell me={me} nav={STAFF_NAV}>
      <PageHeader title={names} actions={
        <div className="flex flex-wrap items-center gap-2">
          {deciding && (
            <>
              <Button variant="secondary"
                onClick={() => record(`/api/leasing/applications/${id}/screen`, {}, () => `Screened ${names}.`)}>
                Screen
              </Button>
              <Button onClick={() => setStep("approve")}>Approve</Button>
              <Button variant="secondary" onClick={() => setStep("reject")}>Reject</Button>
            </>
          )}
          {approved && found.lease_id === null && (
            <LinkButton href={`/draft-leases?application=${id}`}>Create draft lease</LinkButton>
          )}
          {found.lease_id && <LinkButton href={`/leases/${found.lease_id}`}>Open the lease</LinkButton>}
          {menu.length > 0 && <RowMenu label={names} actions={menu} />}
        </div>
      } />
      <p className="flex flex-wrap items-center gap-3 text-base text-ink-muted">
        <GoLink href="/applicants" className={LINK}>Applicants</GoLink>
        <Badge tone={badge.tone} data-testid="status">{badge.word}</Badge>
      </p>
      <Said>{said}</Said>
      {step === null && <Notice tone="error">{refused}</Notice>}
      <Card title="Summary">
        <dl className="grid grid-cols-2 gap-x-6 gap-y-2 text-base md:grid-cols-3" aria-label="Summary">
          <Item label="Unit"><GoLink href={`/units/${found.unit_id}`} className={LINK}>{found.unit}</GoLink></Item>
          <Item label="Property"><GoLink href={`/properties/${found.property_id}`} className={LINK}>{found.property}</GoLink></Item>
          <Item label="Rent asked">{money(found.monthly_rent)}</Item>
          <Item label="Moving in">{usDate(found.move_in_on)}</Item>
          <Item label="Household income a month">{money(found.monthly_income)}</Item>
          <Item label="Income to rent">{incomeMultiple(found.monthly_income, found.monthly_rent)}</Item>
          <Item label="Applied">{when(found.submitted_at)}</Item>
        </dl>
      </Card>
      <Card title="Applicants">
        <ul className="flex flex-col gap-1 text-base" aria-label="Applicants">
          {found.applicants.map((person) => (
            <li key={person.email}>{person.name}, {person.email}</li>
          ))}
        </ul>
      </Card>
      <Card title="Screening">
        {found.screenings.length === 0 ? (
          <p className="text-base text-ink-muted">Not screened yet.</p>
        ) : (
          <ul className="flex flex-col gap-2 text-base" aria-label="Screenings">
            {found.screenings.map((s) => (
              <li key={s.id}>
                <span className="font-medium text-ink">{recommendationLabel(s.recommendation)}</span>
                {s.reasons.length > 0 && `: ${s.reasons.join("; ")}`}
                <span className="block text-sm text-ink-subtle">{s.partner}, {when(s.reported_at)}</span>
              </li>
            ))}
          </ul>
        )}
      </Card>
      {found.decision && (
        <Card title="Decision">
          <div className="flex flex-col gap-2 text-base" data-testid="decision">
            <p>
              {DECIDED[found.decision.decision]} {when(found.decision.decided_at)}
              {found.decision.conditions ? `: ${found.decision.conditions}` : "."}
            </p>
            {found.decision.adverse_action && (
              <div className="flex flex-wrap items-center gap-3">
                <span className="text-ink-muted">
                  Adverse action notice given {usDate(found.decision.adverse_action.notice_given_on)}:{" "}
                  {found.decision.adverse_action.reasons.join("; ")}
                </span>
                <LinkButton href={`/api/leasing/applications/${encodeURIComponent(id)}/notice`}>Notice (PDF)</LinkButton>
              </div>
            )}
          </div>
        </Card>
      )}
      {found.decision?.adverse_action && <EmailsCard about="application" aboutId={id} name={names} />}
      {found.withdrawal && (
        <Card title="Withdrawn">
          <p className="text-base" data-testid="withdrawal">
            {when(found.withdrawal.recorded_at)}
            {found.withdrawal.recorded_by ? `, recorded by ${found.withdrawal.recorded_by}` : ""}
            {found.withdrawal.reason ? `: ${found.withdrawal.reason}` : "."}
          </p>
        </Card>
      )}
      {step !== null && (
        <SidePanel title={TITLES[step]} onClose={() => setStep(null)}>
          <p className="text-base text-ink">{names}, {found.property}, {found.unit}</p>
          {step === "withdraw"
            ? <WithdrawForm application={found} record={recordThenClose} />
            : <DecideForm application={found} rejecting={step === "reject"} record={recordThenClose} />}
          <Notice tone="error">{refused}</Notice>
        </SidePanel>
      )}
    </Shell>
  );
}

function Item({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div>
      <dt className="text-sm text-ink-muted">{label}</dt>
      <dd>{children}</dd>
    </div>
  );
}

// Approves (outright or on conditions) or rejects the application, resting on one of its
// screenings or none; a rejection or a conditional approval that rests on a screening records the
// adverse action notice given the applicants.
export function DecideForm({ application, rejecting, record }: {
  application: Application;
  rejecting: boolean;
  record: Recorder;
}) {
  const last = application.screenings.at(-1);
  const [decision, setDecision] = useState<Decision["decision"]>(rejecting ? "declined" : "approved");
  const [screening, setScreening] = useState(last?.id ?? "");
  const [conditions, setConditions] = useState("");
  const [reasons, setReasons] = useState(last?.reasons.join("\n") ?? "");
  const [noticeOn, setNoticeOn] = useState(day(new Date()));
  const notice = needsNotice(decision, screening || null);
  const names = applicantNames(application);
  return (
    <Form className="flex flex-col gap-3" onSubmit={(event) => {
      event.preventDefault();
      const body = {
        decision,
        conditions: decision === "conditional" ? conditions : null,
        screening_id: screening || null,
        adverse_action: notice
          ? { reasons: reasons.split("\n").filter((reason) => reason.trim()), notice_given_on: noticeOn }
          : null,
      };
      void record(`/api/leasing/applications/${application.id}/decide`, body,
        () => `${DECIDED[decision]}: ${names}.`);
    }}>
      {rejecting ? null : (
        <Select label="Decision" id="decide-decision" value={decision}
          onChange={(e) => setDecision(e.target.value as Decision["decision"])}>
          <option value="approved">Approve</option>
          <option value="conditional">Approve on conditions</option>
        </Select>
      )}
      <Select label="Resting on" id="decide-screening" value={screening} onChange={(e) => setScreening(e.target.value)}>
        <option value="">No screening</option>
        {application.screenings.map((s) => (
          <option key={s.id} value={s.id}>{s.partner}, {when(s.reported_at)}</option>
        ))}
      </Select>
      {decision === "conditional" && (
        <Field label="Conditions" id="decide-conditions" required maxLength={2000} value={conditions}
          onChange={(e) => setConditions(e.target.value)} />
      )}
      {notice && (
        <>
          <label htmlFor="decide-reasons" className="flex flex-col gap-1.5 text-sm text-ink-muted">
            Adverse action reasons, one a line
            <textarea id="decide-reasons" rows={3} required value={reasons}
              onChange={(e) => setReasons(e.target.value)} className={TEXTAREA} />
          </label>
          <Field label="Notice given on" id="decide-notice" type="date" required value={noticeOn}
            onChange={(e) => setNoticeOn(e.target.value)} />
        </>
      )}
      <div>
        <Button type="submit" variant={rejecting ? "danger" : "primary"}>{rejecting ? "Reject" : "Approve"}</Button>
      </div>
    </Form>
  );
}

// Records that the applicants withdrew the application, and why if they said.
function WithdrawForm({ application, record }: { application: Application; record: Recorder }) {
  const [reason, setReason] = useState("");
  return (
    <Form className="flex flex-col gap-3" onSubmit={(event) => {
      event.preventDefault();
      void record(`/api/leasing/applications/${application.id}/withdraw`, { reason: reason.trim() || null },
        () => `Withdrawn: ${applicantNames(application)}.`);
    }}>
      <label htmlFor="withdraw-reason" className="flex flex-col gap-1.5 text-sm text-ink-muted">
        Why (optional)
        <textarea id="withdraw-reason" rows={3} maxLength={500} value={reason}
          onChange={(e) => setReason(e.target.value)} className={TEXTAREA} />
      </label>
      <div><Button type="submit" variant="danger">Record withdrawal</Button></div>
    </Form>
  );
}
