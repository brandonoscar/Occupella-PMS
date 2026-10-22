import { useState } from "react";
import type { Me } from "../api";
import { AuthenticatorSetup } from "../components/AuthenticatorSetup";
import { Button } from "../components/Button";
import { Field } from "../components/Field";
import { Form } from "../components/Form";
import { Notice } from "../components/Notice";
import { Said } from "../components/Said";
import { Card } from "../components/Shell";
import { useRecord } from "./useRecord";

// Whether this person's sign-in asks for a code, and whether their company requires it.
export type TwoStep = { on: boolean; required: boolean };
type Setup = { totp_uri: string; secret: string };

export function twoStepLine(state: TwoStep): string {
  if (state.on) {
    const asks = "On: each sign-in asks for your password and a code from your authenticator app.";
    return state.required ? `${asks} Your property manager requires it, so it stays on.` : asks;
  }
  return state.required
    ? "Off. Your property manager requires it: turn it on here, or you set it up the next time you sign in."
    : "Off: you sign in with your password alone.";
}

const CODE = { inputMode: "numeric", autoComplete: "one-time-code", pattern: "[0-9]{6}", required: true } as const;

// An owner's or a tenant's own two-step sign-in (migration 0153): turned on with an authenticator
// app on their phone, whose first code confirms it, and off with a current code unless their
// company requires it. Staff set theirs up from their invite, so their pages have no such card.
export function TwoStepCard({ me }: { me: Me }) {
  const [state, setState] = useState<TwoStep>({
    on: me.two_step ?? false,
    required: me.two_step_required ?? false,
  });
  const [setup, setSetup] = useState<Setup | null>(null);
  const [code, setCode] = useState("");
  const { said, error, record } = useRecord(() => setCode(""));

  return (
    <Card title="Two-step sign-in">
      <div className="flex flex-col gap-3" role="group" aria-label="Two-step sign-in">
        <p className="text-base" data-testid="two-step-state">{twoStepLine(state)}</p>
        {!state.on && !setup && (
          <div>
            <Button variant="secondary" onClick={() =>
              record<Setup>("/api/me/two-step/start", {}, (found) => {
                setSetup(found);
                return "";
              })}>
              Turn on two-step sign-in
            </Button>
          </div>
        )}
        {!state.on && setup && (
          <Form className="flex flex-col gap-3" onSubmit={(event) => {
            event.preventDefault();
            return record<TwoStep>("/api/me/two-step/confirm", { code }, (found) => {
              setState(found);
              setSetup(null);
              return "Two-step sign-in is on: each sign-in now asks for a code from your authenticator app.";
            });
          }}>
            <p className="text-sm text-ink-muted">
              Scan this with an authenticator app on your phone, or type the key into it, then enter the
              code it shows.
            </p>
            <AuthenticatorSetup uri={setup.totp_uri} />
            <Field label="Code from your authenticator app" id="two-step-code" {...CODE} value={code}
              onChange={(e) => setCode(e.target.value)} />
            <div className="flex flex-wrap gap-2">
              <Button type="submit">Turn on</Button>
              <Button type="button" variant="secondary" onClick={() => setSetup(null)}>Cancel</Button>
            </div>
          </Form>
        )}
        {state.on && !state.required && (
          <Form className="flex flex-wrap items-end gap-3" onSubmit={(event) => {
            event.preventDefault();
            return record<TwoStep>("/api/me/two-step/off", { code }, (found) => {
              setState(found);
              return "Two-step sign-in is off: you sign in with your password alone.";
            });
          }}>
            <Field label="Code from your authenticator app" id="two-step-off-code" {...CODE} value={code}
              onChange={(e) => setCode(e.target.value)} />
            <Button type="submit" variant="secondary">Turn off</Button>
          </Form>
        )}
        <p className="text-sm text-ink-subtle">
          Lost your phone? Ask your property manager for a new invite: it sets a new password, and you set
          two-step sign-in up again.
        </p>
        <Said>{said}</Said>
        <Notice tone="error">{error}</Notice>
      </div>
    </Card>
  );
}
