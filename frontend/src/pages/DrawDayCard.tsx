import { useEffect, useState } from "react";
import { ApiError, api } from "../api";
import { Button } from "../components/Button";
import { Form } from "../components/Form";
import { Notice } from "../components/Notice";
import { Said } from "../components/Said";
import { Select } from "../components/Select";
import { Card } from "../components/Shell";
import { SidePanel } from "../components/SidePanel";
import type { DrawDay } from "../payouts";
import { DRAW_DAY } from "../payouts";
import { ordinal } from "./AutomaticEmailsCard";
import { useRecord } from "./useRecord";

// The company's draw day (ADR 0010 phase 1b): the night of that day each month, the nightly run
// prepares every owner's draw, what each property account has available then, and the
// Dashboard says they are ready. Nothing is paid until staff pay them on Pay owners.
export function drawDayLine(day: Pick<DrawDay, "draw_day">): string {
  return day.draw_day === null
    ? "No draw day: staff pay owners on Pay owners when they choose."
    : `The night of the ${ordinal(day.draw_day)} of each month, every owner's draw is prepared for staff to pay.`;
}

export function DrawDayCard() {
  const [day, setDay] = useState<DrawDay | null>(null);
  const [error, setError] = useState("");
  const [editing, setEditing] = useState(false);
  const [runs, setRuns] = useState(0);
  const { said, error: refused, record } = useRecord(() => setRuns((n) => n + 1));
  useEffect(() => {
    api.get<DrawDay>(DRAW_DAY).then(setDay)
      .catch((failed) => setError(failed instanceof ApiError ? failed.detail : String(failed)));
  }, [runs]);

  return (
    <Card title="Owner draws">
      <Said>{said}</Said>
      {day === null ? (
        <Notice tone={error ? "error" : "info"}>{error || "Loading"}</Notice>
      ) : (
        <>
          <p className="text-base" data-testid="draw-day">{drawDayLine(day)}</p>
          <p className="mt-1 text-sm text-ink-muted">
            Each draw is the account's balance less its reserve and the bills dated by that day. Paying them
            records each owner's payout, by their own way of being paid.
          </p>
          <div className="mt-3">
            <Button variant="secondary" onClick={() => setEditing(true)}>Edit draw day</Button>
          </div>
          {editing && (
            <SidePanel title="Owner draws" onClose={() => setEditing(false)}>
              <DrawDayForm day={day} save={(body) =>
                record(DRAW_DAY, body, () => "Saved the draw day.").then((ok) => {
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

const DAYS = Array.from({ length: 28 }, (_, i) => i + 1);

function DrawDayForm({ day, save }: { day: DrawDay; save: (body: unknown) => Promise<void> }) {
  const [chosen, setChosen] = useState(day.draw_day === null ? "" : String(day.draw_day));
  return (
    <Form className="flex flex-col gap-3" onSubmit={(event) => {
      event.preventDefault();
      return save({ draw_day: chosen === "" ? null : Number(chosen) });
    }}>
      <Select label="Draw day" id="draw-day" value={chosen} onChange={(e) => setChosen(e.target.value)}>
        <option value="">None: pay owners by hand</option>
        {DAYS.map((n) => <option key={n} value={n}>{ordinal(n)} of each month</option>)}
      </Select>
      <div><Button type="submit">Save</Button></div>
    </Form>
  );
}
