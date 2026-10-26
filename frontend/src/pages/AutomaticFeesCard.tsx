import { useEffect, useState } from "react";
import { ApiError, api } from "../api";
import { Button } from "../components/Button";
import { Form } from "../components/Form";
import { Notice } from "../components/Notice";
import { Said } from "../components/Said";
import { Select } from "../components/Select";
import { Card } from "../components/Shell";
import { SidePanel } from "../components/SidePanel";
import { ordinal } from "./AutomaticEmailsCard";
import { useRecord } from "./useRecord";

// Whether the nightly run takes last month's management fees by itself, from a day of the month
// (migration 0082; the founder's answer of 2026-10-18 to q049). Off until a company turns it on;
// staff can always collect them by hand on Management fees.
export type AutomaticFees = {
  automatic: boolean;
  day_of_month: number;
  changed_at: string | null;
  changed_by: string | null;
};

export const AUTOMATIC_FEES = "/api/payables/management-fees/automatic";

// "Taken by themselves: last month's, from the 5th of each month", or how they are taken now.
export function automaticFeesLine(fees: AutomaticFees): string {
  return fees.automatic
    ? `Taken by themselves: last month's, from the ${ordinal(fees.day_of_month)} of each month`
    : "Collected by hand on Management fees";
}

export function AutomaticFeesCard() {
  const [fees, setFees] = useState<AutomaticFees | null>(null);
  const [error, setError] = useState("");
  const [editing, setEditing] = useState(false);
  const [runs, setRuns] = useState(0);
  const { said, error: refused, record } = useRecord(() => setRuns((n) => n + 1));
  useEffect(() => {
    api.get<AutomaticFees>(AUTOMATIC_FEES).then(setFees)
      .catch((failed) => setError(failed instanceof ApiError ? failed.detail : String(failed)));
  }, [runs]);

  return (
    <Card title="Monthly management fees">
      <Said>{said}</Said>
      {fees === null ? (
        <Notice tone={error ? "error" : "info"}>{error || "Loading"}</Notice>
      ) : (
        <>
          <p className="text-base" data-testid="automatic-fees">{automaticFeesLine(fees)}</p>
          <p className="mt-1 text-sm text-ink-muted">
            Each owner's fee comes from their account into the company's fee account, inside trust; one whose
            owner holds too little waits for the next night. Take the company's fees out of trust from Banking.
          </p>
          <div className="mt-3">
            <Button variant="secondary" onClick={() => setEditing(true)}>Edit monthly fees</Button>
          </div>
          {editing && (
            <SidePanel title="Monthly management fees" onClose={() => setEditing(false)}>
              <AutomaticFeesForm fees={fees} save={(body) =>
                record(AUTOMATIC_FEES, body, () => "Saved the monthly management fees.").then((ok) => {
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

function AutomaticFeesForm({ fees, save }: { fees: AutomaticFees; save: (body: unknown) => Promise<void> }) {
  const [automatic, setAutomatic] = useState(fees.automatic);
  const [day, setDay] = useState(fees.day_of_month);
  return (
    <Form className="flex flex-col gap-3" onSubmit={(event) => {
      event.preventDefault();
      return save({ automatic, day_of_month: day });
    }}>
      <label className="flex items-center gap-2 text-sm text-ink">
        <input type="checkbox" checked={automatic} onChange={(e) => setAutomatic(e.target.checked)} />
        Take last month's management fees by themselves
      </label>
      {automatic && (
        <Select label="From this day of the month" id="fee-day" value={day} onChange={(e) => setDay(Number(e.target.value))}>
          {DAYS.map((n) => <option key={n} value={n}>{ordinal(n)}</option>)}
        </Select>
      )}
      <div>
        <Button type="submit" says={automatic
          ? `From the ${ordinal(day)} each month, the nightly run takes last month's fees. They stay in trust until taken out.`
          : "Fees are taken only when staff collect them on Management fees. Nothing is taken as you save."}>
          Save
        </Button>
      </div>
    </Form>
  );
}
