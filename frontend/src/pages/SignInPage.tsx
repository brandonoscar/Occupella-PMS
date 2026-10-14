import { useState } from "react";
import type { Me } from "../api";
import { ApiError, api } from "../api";
import { Button } from "../components/Button";
import { Field } from "../components/Field";
import { Notice } from "../components/Notice";
import { Wordmark } from "../components/Shell";
import { navigate } from "../router";

export const PMC_KEY = "pms.pmc";

// The PMC to sign in to: from the link (?pmc=), or the one this browser signed in to last.
export function pmcToSignIn(search: string, stored: string | null): string {
  return new URLSearchParams(search).get("pmc") ?? stored ?? "";
}

export function SignInPage() {
  const pmcId = pmcToSignIn(window.location.search, localStorage.getItem(PMC_KEY));
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [code, setCode] = useState("");
  const [needsCode, setNeedsCode] = useState(false);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError("");
    try {
      const me = await api.post<Me>("/api/sign-in", {
        pmc_id: pmcId,
        email,
        password,
        ...(needsCode ? { code } : {}),
      });
      localStorage.setItem(PMC_KEY, me.pmc_id);
      navigate("/");
    } catch (failed) {
      if (failed instanceof ApiError && failed.detail.includes("authenticator")) {
        setNeedsCode(true);
      }
      setError(failed instanceof ApiError ? failed.detail : "the server can't be reached");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="mx-auto flex min-h-screen max-w-sm flex-col justify-center gap-6 px-6">
      <Wordmark />
      <h1 className="font-display text-lg font-semibold">Sign in</h1>
      {pmcId ? (
        <form className="flex flex-col gap-4" onSubmit={submit}>
          <Field label="Email" id="email" type="email" autoComplete="username" value={email}
            onChange={(e) => setEmail(e.target.value)} required />
          <Field label="Password" id="password" type="password" autoComplete="current-password"
            value={password} onChange={(e) => setPassword(e.target.value)} required />
          {needsCode && (
            <Field label="Code from your authenticator app" id="code" inputMode="numeric"
              autoComplete="one-time-code" pattern="[0-9]{6}" value={code}
              onChange={(e) => setCode(e.target.value)} required />
          )}
          <Notice tone="error">{error}</Notice>
          <Button type="submit" disabled={busy}>
            Sign in
          </Button>
        </form>
      ) : (
        <Notice tone="info">
          Open the sign-in link your property manager sent you, or the invite link to set your
          password.
        </Notice>
      )}
    </div>
  );
}
