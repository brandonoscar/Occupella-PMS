import { useEffect, useState } from "react";
import type { Me } from "../api";
import { ApiError, api } from "../api";
import { AuthenticatorSetup } from "../components/AuthenticatorSetup";
import { Button } from "../components/Button";
import { Field } from "../components/Field";
import { Notice } from "../components/Notice";
import { Wordmark } from "../components/Shell";
import { navigate } from "../router";
import { PMC_KEY } from "./SignInPage";
import { Form } from "../components/Form";

type Details = { kind: string; email: string; name: string; pmc_name: string; expires_at: string };

// What setting the password answers: the person signed in; an authenticator to set up next (staff,
// or an owner or a tenant whose company requires two-step sign-in); or, for a reset an owner or a
// tenant asked for while their two-step sign-in is on, that they sign in next with a code too.
type Accepted = Me | { totp_uri: string } | { pmc_id: string; sign_in_with_code: true };

// Why the authenticator is set up now, in words for whom the invite is.
export function enrollWords(kind: string): string {
  return kind === "staff"
    ? "Staff also sign in with a code from an authenticator app."
    : "Your property manager requires two-step sign-in: you sign in with a code from an authenticator app too.";
}

// The invite link is /invite#<token>: the token stays in the fragment, which the browser
// never sends to a server.
export function InvitePage() {
  const token = window.location.hash.slice(1);
  const [details, setDetails] = useState<Details | null>(null);
  const [error, setError] = useState("");
  const [password, setPassword] = useState("");
  const [again, setAgain] = useState("");
  const [totpUri, setTotpUri] = useState("");
  const [code, setCode] = useState("");
  const [busy, setBusy] = useState(false);
  // A reset that kept two-step sign-in on: the company to sign in to next.
  const [signInTo, setSignInTo] = useState("");

  useEffect(() => {
    api
      .post<Details>("/api/invites/view", { token })
      .then(setDetails)
      .catch((failed) => setError(failed instanceof ApiError ? failed.detail : String(failed)));
  }, [token]);

  function signedIn(me: Me) {
    localStorage.setItem(PMC_KEY, me.pmc_id);
    navigate("/");
  }

  async function setThePassword(event: React.FormEvent) {
    event.preventDefault();
    if (password !== again) {
      setError("the two passwords are different");
      return;
    }
    setBusy(true);
    setError("");
    try {
      const done = await api.post<Accepted>("/api/invites/accept", { token, password });
      if ("totp_uri" in done) setTotpUri(done.totp_uri);
      else if ("sign_in_with_code" in done) {
        localStorage.setItem(PMC_KEY, done.pmc_id);
        setSignInTo(done.pmc_id);
      } else signedIn(done);
    } catch (failed) {
      setError(failed instanceof ApiError ? failed.detail : String(failed));
    } finally {
      setBusy(false);
    }
  }

  async function confirm(event: React.FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError("");
    try {
      signedIn(await api.post<Me>("/api/invites/confirm", { token, code }));
    } catch (failed) {
      setError(failed instanceof ApiError ? failed.detail : String(failed));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="mx-auto flex min-h-screen max-w-sm flex-col justify-center gap-6 px-6">
      <Wordmark />
      {details && (
        <div className="flex flex-col gap-1">
          <h1 className="font-display text-lg font-semibold">Welcome, {details.name}</h1>
          <p className="text-sm text-ink-muted">
            {details.pmc_name} invited {details.email} to sign in.
          </p>
        </div>
      )}
      {signInTo && (
        <div className="flex flex-col gap-3">
          <Notice tone="info">
            Your new password is set. Two-step sign-in stays on: sign in with your new password and
            the code from your authenticator app.
          </Notice>
          <a href={`/sign-in?pmc=${signInTo}`} className="text-sm text-iris underline hover:text-iris-hover">Sign in</a>
        </div>
      )}
      {details && !totpUri && !signInTo && (
        <Form className="flex flex-col gap-4" onSubmit={setThePassword}>
          <Field label="Choose a password (12 characters or more)" id="password" type="password"
            autoComplete="new-password" minLength={12} value={password}
            onChange={(e) => setPassword(e.target.value)} required />
          <Field label="The same password again" id="again" type="password"
            autoComplete="new-password" value={again} onChange={(e) => setAgain(e.target.value)}
            required />
          <Notice tone="error">{error}</Notice>
          <Button type="submit" disabled={busy}>
            Set password
          </Button>
        </Form>
      )}
      {totpUri && (
        <Form className="flex flex-col gap-4" onSubmit={confirm}>
          <p className="text-sm text-ink-muted">
            {enrollWords(details?.kind ?? "staff")} Scan this with the app, or type the key into it,
            then enter the code it shows.
          </p>
          <AuthenticatorSetup uri={totpUri} />
          <Field label="Code" id="code" inputMode="numeric" autoComplete="one-time-code"
            pattern="[0-9]{6}" value={code} onChange={(e) => setCode(e.target.value)} required />
          <Notice tone="error">{error}</Notice>
          <Button type="submit" disabled={busy}>
            Finish
          </Button>
        </Form>
      )}
      {!details && <Notice tone="error">{error}</Notice>}
    </div>
  );
}
