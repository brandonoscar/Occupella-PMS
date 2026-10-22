import { useEffect, useState } from "react";
import { ApiError, api } from "../api";
import { Button } from "../components/Button";
import { useConfirm } from "../components/Confirm";
import { Notice } from "../components/Notice";
import { Said } from "../components/Said";
import { Card } from "../components/Shell";
import { usDate } from "../money";
import { useRecord } from "./useRecord";

// Whether the company requires two-step sign-in of its owners and tenants (migration 0153), and
// who last changed it. Staff who may change who can sign in change it; every staff member reads it.
export type TwoStepRequired = { required: boolean; changed_at: string | null; changed_by: string | null };

export const TWO_STEP_REQUIRED = "/api/logins/two-step";

export function requiredLine(setting: TwoStepRequired): string {
  return setting.required
    ? "Required: every owner and tenant signs in with a code from an authenticator app too. One who" +
        " hasn't set it up does so at their next sign-in."
    : "Each owner and tenant chooses whether to turn it on in their portal. Staff always sign in with a code.";
}

export function changedLine(setting: TwoStepRequired): string {
  if (!setting.changed_at) return "";
  return `Changed${setting.changed_by ? ` by ${setting.changed_by}` : ""} on ${usDate(setting.changed_at)}.`;
}

// Settings → Logins: the company's rule for owners' and tenants' sign-in. Requiring it signs no one
// out; it applies at each one's next sign-in, and an admin's new invite is how one who lost their
// phone gets back in.
export function TwoStepRequiredCard() {
  const [setting, setSetting] = useState<TwoStepRequired | null>(null);
  const [loadError, setLoadError] = useState("");
  const { ask, prompt } = useConfirm();
  const { said, error, record } = useRecord(() => undefined);
  useEffect(() => {
    api.get<TwoStepRequired>(TWO_STEP_REQUIRED).then(setSetting)
      .catch((failed) => setLoadError(failed instanceof ApiError ? failed.detail : String(failed)));
  }, []);

  function change(required: boolean) {
    return record<TwoStepRequired>(TWO_STEP_REQUIRED, { required }, (found) => {
      setSetting(found);
      return required
        ? "Owners and tenants now sign in with a code too, each from their next sign-in."
        : "Owners and tenants now choose whether to sign in with a code.";
    });
  }

  return (
    <Card title="Two-step sign-in for owners and tenants">
      {setting === null ? (
        <Notice tone={loadError ? "error" : "info"}>{loadError || "Loading"}</Notice>
      ) : (
        <div className="flex flex-col gap-3">
          <p className="text-base" data-testid="two-step-required">{requiredLine(setting)}</p>
          {setting.changed_at && <p className="text-sm text-ink-subtle">{changedLine(setting)}</p>}
          <div>
            {setting.required ? (
              <Button variant="secondary" onClick={() => ask(
                "Stop requiring two-step sign-in? Owners and tenants who turned it on keep it until they turn it off.",
                () => change(false),
                "Stop requiring it",
              )}>Stop requiring it</Button>
            ) : (
              <Button variant="secondary" onClick={() => ask(
                "Require two-step sign-in of every owner and tenant? No one is signed out: each one without it sets it up at their next sign-in.",
                () => change(true),
                "Require it",
              )}>Require two-step sign-in</Button>
            )}
          </div>
          <Said>{said}</Said>
          <Notice tone="error">{error}</Notice>
        </div>
      )}
      {prompt}
    </Card>
  );
}
