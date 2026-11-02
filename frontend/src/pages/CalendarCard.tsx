import { useEffect, useState } from "react";
import type { Me } from "../api";
import { ApiError, api } from "../api";
import { Button } from "../components/Button";
import { useConfirm } from "../components/Confirm";
import { Form } from "../components/Form";
import { Notice } from "../components/Notice";
import { Said } from "../components/Said";
import type { Groups } from "../components/ScopePicker";
import { Select } from "../components/Select";
import { Card, PageHeader, Shell } from "../components/Shell";
import { usDate } from "../money";
import { STAFF_NAV } from "./staffNav";
import { useRecord } from "./useRecord";

export type CalendarState = { made_at: string | null; group_id: string | null };

// The same link for a calendar app that opens it on this device (Apple Calendar, Outlook).
export function webcal(url: string): string {
  return url.replace(/^https?:/, "webcal:");
}

// A person's own calendar link (migration 0098): their calendar app (Google, Apple, Outlook)
// subscribes to it and shows what is coming. It is shown once when made, as an invite's link is;
// a new one takes the old back, and taking it back stops the calendar updating. Staff may show
// one property group's properties only.
export function CalendarCard({ path, intro, groups }: {
  path: "/api/calendar/mine" | "/api/tenant/calendar";
  intro: string;
  groups?: Groups["groups"];
}) {
  const [state, setState] = useState<CalendarState | null>(null);
  const [loadError, setLoadError] = useState("");
  const [url, setUrl] = useState("");
  const [group, setGroup] = useState("");
  const { ask, prompt } = useConfirm();

  function refresh() {
    api.get<CalendarState>(path)
      .then((found) => {
        setState(found);
        setGroup(found.group_id ?? "");
      })
      .catch((failed) => setLoadError(failed instanceof ApiError ? failed.detail : String(failed)));
  }
  useEffect(refresh, [path]);
  const { said, error, record } = useRecord(refresh);

  const standing = state?.made_at ? state : null;
  const shows = groups?.find((g) => g.id === standing?.group_id)?.name;
  return (
    <Card title="Calendar">
      <p className="mb-3 text-base text-ink-muted">{intro}</p>
      {state === null ? (
        <Notice tone={loadError ? "error" : "info"}>{loadError || "Loading"}</Notice>
      ) : (
        <div className="flex flex-col gap-3">
          <p className="text-base" data-testid="calendar-state">
            {standing
              ? `Your calendar link was made on ${usDate(standing.made_at)}${shows ? `, for ${shows}` : ""}.`
              : "You have no calendar link."}
          </p>
          <Form className="flex flex-wrap items-end gap-3" onSubmit={(event) => {
            event.preventDefault();
            const body = groups ? { group_id: group || null } : {};
            return record<{ url: string }>(path, body, (made) => {
              setUrl(made.url);
              return standing
                ? "Made a new calendar link; the one before stopped working."
                : "Made your calendar link.";
            });
          }}>
            {groups && (
              <Select label="Show" id="calendar-group" value={group} onChange={(event) => setGroup(event.target.value)}>
                <option value="">All properties</option>
                {groups.map((g) => <option key={g.id} value={g.id}>{g.name}</option>)}
              </Select>
            )}
            <Button type="submit" variant="secondary" says={standing
              ? "Makes a new link, shown here once. The old one stops working, so your calendar app needs the new one."
              : "Makes a link your calendar app subscribes to, shown here once. Nothing is emailed or shared."}>
              {standing ? "Make a new link" : "Make a calendar link"}
            </Button>
            {standing && (
              <Button type="button" variant="secondary" onClick={() => ask(
                "Take your calendar link back? Your calendar app stops getting updates. You can make a new link any time.",
                () => record<null>(`${path}/revoke`, {}, () => {
                  setUrl("");
                  return "Took your calendar link back.";
                }),
                "Take it back",
              )}>Take the link back</Button>
            )}
          </Form>
          {url && (
            <div className="flex flex-col gap-2">
              <p className="text-sm text-ink-subtle">
                Shown once: copy it now. In Google Calendar, add it under Other calendars, From URL; in
                Outlook, Add calendar, Subscribe from web. Anyone with the link sees this calendar, so
                keep it to yourself.
              </p>
              <input readOnly aria-label="Calendar link" value={url}
                className="w-full rounded-sm border border-line-strong bg-sunken px-2 py-1.5 font-mono text-sm break-all text-ink" />
              <a href={webcal(url)} className="text-sm text-iris hover:text-iris-hover hover:underline">
                Open in the calendar app on this device
              </a>
            </div>
          )}
          <Said>{said}</Said>
          <Notice tone="error">{error}</Notice>
        </div>
      )}
      {prompt}
    </Card>
  );
}

// Settings → Your calendar: a staff member's own calendar of the company's coming days.
export function CalendarPage({ me }: { me: Me }) {
  const [groups, setGroups] = useState<Groups["groups"]>([]);
  useEffect(() => {
    api.get<Groups>("/api/property-groups").then((found) => setGroups(found.groups)).catch(() => setGroups([]));
  }, []);
  return (
    <Shell me={me} nav={STAFF_NAV}>
      <PageHeader title="Your calendar" />
      <CalendarCard path="/api/calendar/mine" groups={groups}
        intro={"Move-ins and leases ending, repair visits and work orders due, showings, bills due, rent"
          + " changes, insurance running out and the day to take the company's fees out of trust, in"
          + " your own calendar app. It updates by itself, as often as the app checks."} />
    </Shell>
  );
}
