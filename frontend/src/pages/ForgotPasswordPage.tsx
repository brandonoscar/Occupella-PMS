import { useState } from "react";
import { ApiError, api } from "../api";
import { Button } from "../components/Button";
import { Field } from "../components/Field";
import { Form } from "../components/Form";
import { Notice } from "../components/Notice";
import { Wordmark } from "../components/Shell";
import { PMC_KEY, pmcToSignIn } from "./SignInPage";

// A forgotten password (ADR 0007): an owner or a tenant gives their email and gets a link that
// sets a new one. The answer is the same whether or not the email has a login, so it tells no
// one who does. Staff ask an admin for a new invite instead, which keeps their authenticator.
export function ForgotPasswordPage() {
  const pmcId = pmcToSignIn(window.location.search, localStorage.getItem(PMC_KEY));
  const [email, setEmail] = useState("");
  const [answer, setAnswer] = useState("");
  const [error, setError] = useState("");

  return (
    <div className="mx-auto flex min-h-screen max-w-sm flex-col justify-center gap-6 px-6">
      <Wordmark />
      <h1 className="font-display text-lg font-semibold">Set a new password</h1>
      {!pmcId ? (
        <Notice tone="info">Open the sign-in link your property manager sent you first.</Notice>
      ) : answer ? (
        <Notice tone="info">{answer}</Notice>
      ) : (
        <Form className="flex flex-col gap-4" onSubmit={async (event) => {
          event.preventDefault();
          setError("");
          try {
            const asked = await api.post<{ detail: string }>("/api/password-reset", { pmc_id: pmcId, email });
            setAnswer(asked.detail);
          } catch (failed) {
            setError(failed instanceof ApiError ? failed.detail : "the server can't be reached");
          }
        }}>
          <Field label="Email you sign in with" id="email" type="email" autoComplete="username" required
            value={email} onChange={(event) => setEmail(event.target.value)} />
          <p className="text-sm text-ink-muted">
            For owners and tenants. Staff: ask an admin for a new invite.
          </p>
          <Notice tone="error">{error}</Notice>
          <Button type="submit"
            says="Emails a link to set a new password, if this email signs in here. Your password works until you use it.">
            Email me a link
          </Button>
        </Form>
      )}
      <a href={pmcId ? `/sign-in?pmc=${pmcId}` : "/sign-in"} className="text-sm text-iris underline hover:text-iris-hover">Back to sign in</a>
    </div>
  );
}
