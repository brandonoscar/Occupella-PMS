import { useEffect, useState } from "react";
import { ApiError, api } from "../api";
import { Button } from "../components/Button";
import { Form } from "../components/Form";
import { Notice } from "../components/Notice";
import { Said } from "../components/Said";
import { Card } from "../components/Shell";
import { SidePanel } from "../components/SidePanel";
import { useRecord } from "./useRecord";

// Whether tenants' prepaid rent pays what their lease owes by itself, each night once new charges
// post (migration 0117; the founder's review q031). Off until a company turns it on; staff can
// always use it by hand with Use prepaid rent on the lease.
export type AutomaticPrepaid = {
  automatic: boolean;
  changed_at: string | null;
  changed_by: string | null;
};

const AUTOMATIC_PREPAID = "/api/rent/prepaid/automatic";

export function automaticPrepaidLine(prepaid: AutomaticPrepaid): string {
  return prepaid.automatic
    ? "Used by itself each night for what the lease owes"
    : "Used by hand with Use prepaid rent on the lease";
}

export function AutomaticPrepaidCard() {
  const [prepaid, setPrepaid] = useState<AutomaticPrepaid | null>(null);
  const [error, setError] = useState("");
  const [editing, setEditing] = useState(false);
  const [runs, setRuns] = useState(0);
  const { said, error: refused, record } = useRecord(() => setRuns((n) => n + 1));
  useEffect(() => {
    api.get<AutomaticPrepaid>(AUTOMATIC_PREPAID).then(setPrepaid)
      .catch((failed) => setError(failed instanceof ApiError ? failed.detail : String(failed)));
  }, [runs]);

  return (
    <Card title="Prepaid rent">
      <Said>{said}</Said>
      {prepaid === null ? (
        <Notice tone={error ? "error" : "info"}>{error || "Loading"}</Notice>
      ) : (
        <>
          <p className="text-base" data-testid="automatic-prepaid">{automaticPrepaidLine(prepaid)}</p>
          <p className="mt-1 text-sm text-ink-muted">
            A tenant's payment over what the lease owes is held as their prepaid rent. Used by itself, it pays the
            rent first, oldest first, once a day per lease; a lease on a payment hold, or at a property whose owner's
            account staff choose, is left for staff.
          </p>
          <div className="mt-3">
            <Button variant="secondary" onClick={() => setEditing(true)}>Edit prepaid rent</Button>
          </div>
          {editing && (
            <SidePanel title="Prepaid rent" onClose={() => setEditing(false)}>
              <AutomaticPrepaidForm prepaid={prepaid} save={(body) =>
                record(AUTOMATIC_PREPAID, body, () => "Saved how prepaid rent is used.").then((ok) => {
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

function AutomaticPrepaidForm({ prepaid, save }: { prepaid: AutomaticPrepaid; save: (body: unknown) => Promise<void> }) {
  const [automatic, setAutomatic] = useState(prepaid.automatic);
  return (
    <Form className="flex flex-col gap-3" onSubmit={(event) => {
      event.preventDefault();
      return save({ automatic });
    }}>
      <label className="flex items-center gap-2 text-sm text-ink">
        <input type="checkbox" checked={automatic} onChange={(e) => setAutomatic(e.target.checked)} />
        Use tenants' prepaid rent by itself for new rent
      </label>
      <div>
        <Button type="submit" says={automatic
          ? "Each night, tenants' prepaid rent pays what their lease owes. Nothing is used as you save."
          : "Prepaid rent waits for staff to use it on the lease. Nothing is used as you save."}>
          Save
        </Button>
      </div>
    </Form>
  );
}
