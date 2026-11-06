import { useEffect, useState } from "react";
import { ApiError, api } from "../api";
import { Button } from "../components/Button";
import { Field } from "../components/Field";
import { Form } from "../components/Form";
import { Notice } from "../components/Notice";
import { Said } from "../components/Said";
import { Card } from "../components/Shell";
import { qrDataUrl, secretOf } from "./InvitePage";
import { useRecord } from "./useRecord";

// An owner's or a tenant's two-step sign-in, on their Profile (src/pms/api/two_step.py): theirs to
// turn on, with an authenticator app on their phone. Its first code turns it on; from then on
// signing in asks for a code, and turning it off takes one. Someone who lost their phone asks the
// company for a new invite, which turns it off.
export function TwoStepCard() {
  const [on, setOn] = useState<boolean | null>(null);
  const [loadError, setLoadError] = useState("");
  const [uri, setUri] = useState("");
  const [code, setCode] = useState("");

  function refresh() {
    api.get<{ on: boolean }>("/api/me/two-step")
      .then((found) => setOn(found.on))
      .catch((failed) => setLoadError(failed instanceof ApiError ? failed.detail : String(failed)));
  }
  useEffect(refresh, []);
  const { said, error, record } = useRecord(refresh);

  const codeField = (
    <Field label="Code from your authenticator app" id="two-step-code" inputMode="numeric"
      autoComplete="one-time-code" pattern="[0-9]{6}" maxLength={6} value={code}
      onChange={(event) => setCode(event.target.value)} required />
  );

  return (
    <Card title="Two-step sign-in">
      {on === null ? (
        <Notice tone={loadError ? "error" : "info"}>{loadError || "Loading"}</Notice>
      ) : (
        <div className="flex flex-col gap-3">
          <p className="text-base" data-testid="two-step-state">
            {on
              ? "Two-step sign-in is on: signing in asks for a code from your authenticator app too."
              : "Two-step sign-in is off: signing in asks for your password alone."}
          </p>
          {!on && !uri && (
            <div className="flex flex-col gap-3">
              <p className="text-base text-ink-muted">
                Turn it on and nobody signs in as you with your password alone. You need an
                authenticator app on your phone, such as Google Authenticator, Microsoft
                Authenticator or 1Password.
              </p>
              <Button type="button" variant="secondary" className="self-start" onClick={() =>
                record<{ uri: string }>("/api/me/two-step/start", {}, (started) => {
                  setUri(started.uri);
                  setCode("");
                  return "";
                })}>
                Set up two-step sign-in
              </Button>
            </div>
          )}
          {!on && uri && (
            <Form className="flex flex-col gap-3" onSubmit={(event) => {
              event.preventDefault();
              return record<{ on: boolean }>("/api/me/two-step/confirm", { code }, () => {
                setUri("");
                setCode("");
                return "Two-step sign-in is on. Anywhere else you were signed in, you're signed out.";
              });
            }}>
              <p className="text-base text-ink-muted">
                Scan this with your authenticator app, or type the key into it, then enter the code
                it shows.
              </p>
              <img src={qrDataUrl(uri)} alt="Authenticator setup code" className="self-start rounded-sm" />
              <code className="font-mono text-sm break-all text-ink-muted">{secretOf(uri)}</code>
              {codeField}
              <div className="flex flex-wrap gap-3">
                <Button type="submit">Turn on two-step sign-in</Button>
                <Button type="button" variant="secondary" onClick={() => {
                  setUri("");
                  setCode("");
                }}>Cancel</Button>
              </div>
            </Form>
          )}
          {on && (
            <Form className="flex flex-col gap-3" onSubmit={(event) => {
              event.preventDefault();
              return record<{ on: boolean }>("/api/me/two-step/off", { code }, () => {
                setCode("");
                return "Two-step sign-in is off.";
              });
            }}>
              <p className="text-base text-ink-muted">
                Lost your phone? Ask your property manager for a new invite: setting your password
                from it turns two-step sign-in off.
              </p>
              {codeField}
              <Button type="submit" variant="secondary" className="self-start">
                Turn off two-step sign-in
              </Button>
            </Form>
          )}
          <Said>{said}</Said>
          <Notice tone="error">{error}</Notice>
        </div>
      )}
    </Card>
  );
}
