import { useEffect, useState } from "react";
import { ApiError, api } from "../api";
import { Button, LinkButton } from "../components/Button";
import { Field } from "../components/Field";
import { Notice } from "../components/Notice";
import { Card } from "../components/Shell";
import type { Application, Decision } from "../leasing";
import { incomeMultiple, needsNotice, recommendationLabel } from "../leasing";
import { when } from "../maintenance";
import { day, money, usDate } from "../money";
import { Said } from "../components/Said";
import { Form } from "../components/Form";

type Unit = { unit_id: string; property: string; unit: string; leased_today: boolean };
type Link = {
  id: string;
  property: string;
  unit: string;
  monthly_rent: string;
  opened_at: string;
  applications: number;
  // Its listing on the PMC's public vacancy page, while it is there.
  listing_id: string | null;
};

type Draft = {
  decision: Decision["decision"];
  screeningId: string;
  conditions: string;
  reasons: string;
  noticeOn: string;
};

const SELECT = "h-9 rounded-sm border border-line-strong bg-sunken px-2 text-sm text-ink";

// Staff's leasing: application links for units, each of which may be listed on the PMC's public
// vacancy page, and the applications that come through them, screened through the server's
// partner and decided. A decline or conditional approval that rests on a screening records its
// adverse action notice with it.
export function LeasingCard({
  pmcId,
  onChange,
  part = "all",
}: {
  pmcId?: string;
  onChange?: () => void;
  // Leasing's pages each show a part (ADR 0005): Listings the links, Applicants the applications.
  part?: "all" | "links" | "applications";
}) {
  const [units, setUnits] = useState<Unit[]>([]);
  const [links, setLinks] = useState<Link[]>([]);
  // Every application, decided or not: the open ones to decide, the decided ones' notices.
  const [all, setAll] = useState<Application[] | null>(null);
  const [unit, setUnit] = useState("");
  const [rent, setRent] = useState("");
  const [opened, setOpened] = useState("");
  const [drafts, setDrafts] = useState<Record<string, Draft>>({});
  const [descriptions, setDescriptions] = useState<Record<string, string>>({});
  const [error, setError] = useState("");
  const [said, setSaid] = useState("");

  function show(failed: unknown) {
    setError(failed instanceof ApiError ? failed.detail : String(failed));
  }

  function refresh() {
    api.get<Link[]>("/api/leasing/links").then(setLinks).catch(show);
    api.get<Application[]>("/api/leasing/applications?show=all").then(setAll).catch(show);
  }

  useEffect(() => {
    api.get<Unit[]>("/api/leasing/units").then(setUnits).catch(show);
    refresh();
  }, []);

  // Runs one write and says what it did (`done`), or shows why it was refused.
  async function act(work: () => Promise<unknown>, done = "") {
    setError("");
    setSaid("");
    try {
      await work();
      setSaid(done);
      refresh();
      onChange?.();
    } catch (failed) {
      show(failed);
    }
  }

  function draftOf(application: Application): Draft {
    return (
      drafts[application.id] ?? {
        decision: "approved",
        screeningId: application.screenings.at(-1)?.id ?? "",
        conditions: "",
        reasons: application.screenings.at(-1)?.reasons.join("\n") ?? "",
        noticeOn: day(new Date()),
      }
    );
  }

  function edit(application: Application, change: Partial<Draft>) {
    setDrafts({ ...drafts, [application.id]: { ...draftOf(application), ...change } });
  }

  function decide(application: Application) {
    const draft = draftOf(application);
    const screeningId = draft.screeningId || null;
    return act(() =>
      api.post(`/api/leasing/applications/${application.id}/decide`, {
        decision: draft.decision,
        conditions: draft.decision === "conditional" ? draft.conditions : null,
        screening_id: screeningId,
        adverse_action: needsNotice(draft.decision, screeningId)
          ? {
              reasons: draft.reasons.split("\n").filter((reason) => reason.trim()),
              notice_given_on: draft.noticeOn,
            }
          : null,
      }),
      `Decision on ${application.applicants.map((a) => a.name).join(", ")} recorded.`,
    );
  }

  const applications = all && all.filter((a) => a.decision === null);
  const notices = (all ?? []).filter((a) => a.decision?.adverse_action);

  return (
    <Card title={{ all: "Leasing", links: "Application links", applications: "Applications" }[part]}>
      <Said>{said}</Said>
      <Notice tone="error">{error}</Notice>
      {part !== "applications" && (
      <>
      <h3 className="mb-3 text-md font-medium">Application links</h3>
      <Form
        className="mb-4 flex flex-wrap items-end gap-3"
        onSubmit={(event) => {
          event.preventDefault();
          act(async () => {
            const link = await api.post<{ url: string }>("/api/leasing/links", {
              unit_id: unit,
              monthly_rent: rent,
            });
            setOpened(link.url);
          });
        }}
      >
        <label className="flex flex-col gap-1.5 text-sm text-ink-muted">
          Unit
          <select className={SELECT} required value={unit} onChange={(e) => setUnit(e.target.value)}>
            <option value="">Choose a unit</option>
            {units.map((u) => (
              <option key={u.unit_id} value={u.unit_id}>
                {u.property}, {u.unit}
                {u.leased_today ? " (leased)" : ""}
              </option>
            ))}
          </select>
        </label>
        <Field label="Rent a month" id="link-rent" inputMode="decimal" required value={rent}
          onChange={(e) => setRent(e.target.value)} />
        <Button type="submit" variant="secondary">
          Open a link
        </Button>
      </Form>
      {opened && (
        <p className="mb-4 text-sm text-ink-muted">
          Post this link where applicants will see it. It is shown once:{" "}
          <span className="font-mono break-all text-ink" data-testid="application-link">
            {opened}
          </span>
        </p>
      )}
      <ul className="mb-4 flex flex-col divide-y divide-line">
        {links.map((link) => (
          <li key={link.id} className="flex flex-col gap-2 py-2" aria-label={`${link.property}, ${link.unit}`}>
            <div className="flex items-center justify-between gap-4">
              <span>
                {link.property}, {link.unit} at {money(link.monthly_rent)}.{" "}
                <span className="text-sm text-ink-subtle">
                  {link.applications} {link.applications === 1 ? "application" : "applications"}
                  {link.listing_id ? "; on the public page" : ""}
                </span>
              </span>
              <span className="flex gap-2">
                {link.listing_id && (
                  <Button variant="ghost"
                    onClick={() => act(() => api.post(`/api/leasing/listings/${link.listing_id}/remove`),
                      "Taken off the public page.")}>
                    Take off the page
                  </Button>
                )}
                <Button variant="ghost"
                  onClick={() => act(() => api.post(`/api/leasing/links/${link.id}/close`), "Link closed.")}>
                  Close
                </Button>
              </span>
            </div>
            {!link.listing_id && (
              <Form
                className="flex flex-wrap items-end gap-3"
                onSubmit={(event) => {
                  event.preventDefault();
                  act(() =>
                    api.post(`/api/leasing/links/${link.id}/listing`, {
                      description: (descriptions[link.id] ?? "").trim() || null,
                    }),
                    "Listed on the public page.",
                  );
                }}
              >
                <Field label="Description for the public page (optional)" id={`describe-${link.id}`}
                  maxLength={2000} value={descriptions[link.id] ?? ""}
                  onChange={(e) => setDescriptions({ ...descriptions, [link.id]: e.target.value })} />
                <Button type="submit" variant="secondary">List on the public page</Button>
              </Form>
            )}
          </li>
        ))}
      </ul>
      {pmcId && (
        <p className="mb-8 text-sm text-ink-muted">
          Your public vacancy page:{" "}
          <a className="font-mono break-all text-ink underline" href={`/listings?pmc=${pmcId}`}>
            /listings?pmc={pmcId}
          </a>
          . Point applicants to it; nothing is sent to listing sites.
        </p>
      )}
      </>
      )}

      {part !== "links" && (
      <>
      <h3 className="mb-3 text-md font-medium">Applications to decide</h3>
      {applications === null ? (
        <Notice tone="info">Loading</Notice>
      ) : applications.length === 0 ? (
        <Notice tone="info">No applications are waiting.</Notice>
      ) : (
        <ul className="flex flex-col divide-y divide-line">
          {applications.map((application) => {
            const draft = draftOf(application);
            const notice = needsNotice(draft.decision, draft.screeningId || null);
            return (
              <li key={application.id} className="flex flex-col gap-3 py-4"
                aria-label={`Application from ${application.applicants[0]?.name ?? ""}`}>
                <p>
                  {application.applicants.map((a) => a.name).join(", ")} for {application.unit},{" "}
                  moving in {usDate(application.move_in_on)}.
                </p>
                <p className="text-sm text-ink-subtle">
                  Income {money(application.monthly_income)} a month,{" "}
                  {incomeMultiple(application.monthly_income, application.monthly_rent)} the rent
                  of {money(application.monthly_rent)}. Sent {when(application.submitted_at)}.
                </p>
                {application.screenings.map((s) => (
                  <p key={s.id} className="text-sm text-ink-muted">
                    {s.partner}: {recommendationLabel(s.recommendation)}
                    {s.reasons.length > 0 && ` (${s.reasons.join("; ")})`}
                  </p>
                ))}
                <div>
                  <Button variant="secondary"
                    onClick={() => act(() => api.post(`/api/leasing/applications/${application.id}/screen`),
                      "Sent for screening.")}>
                    Screen
                  </Button>
                </div>
                <div className="flex flex-wrap items-end gap-3">
                  <label className="flex flex-col gap-1.5 text-sm text-ink-muted">
                    Decision
                    <select className={SELECT} value={draft.decision}
                      onChange={(e) => edit(application, { decision: e.target.value as Draft["decision"] })}>
                      <option value="approved">Approve</option>
                      <option value="conditional">Approve on conditions</option>
                      <option value="declined">Decline</option>
                    </select>
                  </label>
                  <label className="flex flex-col gap-1.5 text-sm text-ink-muted">
                    Resting on
                    <select className={SELECT} value={draft.screeningId}
                      onChange={(e) => edit(application, { screeningId: e.target.value })}>
                      <option value="">No screening</option>
                      {application.screenings.map((s) => (
                        <option key={s.id} value={s.id}>
                          {s.partner}, {when(s.reported_at)}
                        </option>
                      ))}
                    </select>
                  </label>
                  {draft.decision === "conditional" && (
                    <Field label="Conditions" id={`conditions-${application.id}`} required
                      value={draft.conditions}
                      onChange={(e) => edit(application, { conditions: e.target.value })} />
                  )}
                </div>
                {notice && (
                  <div className="flex flex-col gap-3 rounded-sm border border-line p-3">
                    <p className="text-sm text-ink-muted">
                      This decision rests on a screening, so it records the adverse action notice
                      you give the applicants.
                    </p>
                    <label htmlFor={`reasons-${application.id}`}
                      className="flex flex-col gap-1.5 text-sm text-ink-muted">
                      Reasons, one a line
                      <textarea id={`reasons-${application.id}`} rows={2} value={draft.reasons}
                        onChange={(e) => edit(application, { reasons: e.target.value })}
                        className="rounded-sm border border-line-strong bg-sunken px-3 py-2 text-base text-ink" />
                    </label>
                    <Field label="Notice given on" id={`notice-${application.id}`} type="date"
                      value={draft.noticeOn}
                      onChange={(e) => edit(application, { noticeOn: e.target.value })} />
                  </div>
                )}
                <div>
                  <Button onClick={() => decide(application)}>Record decision</Button>
                </div>
              </li>
            );
          })}
        </ul>
      )}
      {notices.length > 0 && (
        <>
          <h3 className="mt-8 mb-3 text-md font-medium">Adverse action notices</h3>
          <ul className="flex flex-col gap-2">
            {notices.map((application) => (
              <li key={application.id} className="flex items-center justify-between gap-4">
                <span>
                  {application.applicants.map((a) => a.name).join(", ")}, {application.unit}:{" "}
                  {application.decision?.decision}
                </span>
                <LinkButton
                  href={`/api/leasing/applications/${encodeURIComponent(application.id)}/notice`}>
                  Notice (PDF)
                </LinkButton>
              </li>
            ))}
          </ul>
        </>
      )}
      </>
      )}
    </Card>
  );
}
