import qrcode from "qrcode-generator";
import { useEffect, useState } from "react";
import type { Me } from "../api";
import { ApiError, api } from "../api";
import { Button } from "../components/Button";
import { Field } from "../components/Field";
import { Notice } from "../components/Notice";
import { Wordmark } from "../components/Shell";
import { navigate } from "../router";
import { PMC_KEY } from "./SignInPage";

type Details = { kind: string; email: string; name: string; pmc_name: string; expires_at: string };

// An authenticator app reads the setup link as a QR code; the key is shown too, to type in.
export function qrDataUrl(uri: string): string {
  const code = qrcode(0, "M");
  code.addData(uri);
  code.make();
  return code.createDataURL(4, 8);
}

export function secretOf(uri: string): string {
  return new URL(uri).searchParams.get("secret") ?? "";
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
      const done = await api.post<Me | { totp_uri: string }>("/api/invites/accept", {
        token,
        password,
      });
      if ("totp_uri" in done) setTotpUri(done.totp_uri);
      else signedIn(done);
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
      {details && !totpUri && (
        <form className="flex flex-col gap-4" onSubmit={setThePassword}>
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
        </form>
      )}
      {totpUri && (
        <form className="flex flex-col gap-4" onSubmit={confirm}>
          <p className="text-sm text-ink-muted">
            Staff also sign in with a code from an authenticator app. Scan this with the app, or
            type the key into it, then enter the code it shows.
          </p>
          <img src={qrDataUrl(totpUri)} alt="Authenticator setup code" className="self-start rounded-sm" />
          <code className="font-mono text-sm break-all text-ink-muted">{secretOf(totpUri)}</code>
          <Field label="Code" id="code" inputMode="numeric" autoComplete="one-time-code"
            pattern="[0-9]{6}" value={code} onChange={(e) => setCode(e.target.value)} required />
          <Notice tone="error">{error}</Notice>
          <Button type="submit" disabled={busy}>
            Finish
          </Button>
        </form>
      )}
      {!details && <Notice tone="error">{error}</Notice>}
    </div>
  );
}
