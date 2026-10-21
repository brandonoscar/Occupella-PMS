import { useEffect, useState } from "react";
import { ApiError, api, saveFile } from "../api";
import { Badge } from "../components/Badge";
import { Button } from "../components/Button";
import { useConfirm } from "../components/Confirm";
import { Field } from "../components/Field";
import { Form } from "../components/Form";
import { Notice } from "../components/Notice";
import { Card, Wordmark } from "../components/Shell";
import { SignaturePad } from "../components/SignaturePad";
import { money, usDate } from "../money";
import type { SigningView } from "../signing";

function detail(failed: unknown): string {
  return failed instanceof ApiError ? failed.detail : String(failed);
}

// The PMS's own signing page, /sign#<token> (ADR 0008; src/pms/api/signing.py): the link a signer
// was sent, with no login (the token travels in each request's body). They read the disclosures
// and agree to sign electronically, or ask for paper; open the lease; and sign it with their name
// typed and maybe their signature drawn. Then they keep the lease and its signing certificate.
export function SignPage() {
  const token = window.location.hash.slice(1);
  const [view, setView] = useState<SigningView | null>(null);
  const [error, setError] = useState("");
  const { ask, prompt } = useConfirm();
  useEffect(() => {
    api.post<SigningView>("/api/signing/view", { token }).then(setView).catch((failed) => setError(detail(failed)));
  }, [token]);

  async function step(path: string, body: Record<string, unknown> = {}) {
    setError("");
    try {
      setView(await api.post<SigningView>(`/api/signing/${path}`, { token, ...body }));
    } catch (failed) {
      setError(detail(failed));
    }
  }
  async function save(path: string, name: string) {
    setError("");
    try {
      await saveFile(`/api/signing/${path}`, { token }, name);
      if (path === "document") setView((seen) => seen && { ...seen, opened: true });
    } catch (failed) {
      setError(detail(failed));
    }
  }
  const paper = () =>
    ask("Sign on paper instead? This link will no longer sign the lease.",
      () => step("paper"), "Sign on paper");

  return (
    <div className="mx-auto flex max-w-2xl flex-col gap-6 px-6 py-10">
      <Wordmark />
      {view === null ? (
        <Notice tone={error ? "error" : "info"}>{error || "Loading"}</Notice>
      ) : (
        <>
          <h1 className="font-display text-xl text-ink">Sign your lease with {view.company}</h1>
          <Card title={view.document_title}>
            <section aria-label="The lease" className="flex flex-col gap-2 text-base text-ink">
              <p>{view.home}</p>
              <p>
                From {usDate(view.starts_on)}, {view.ends_on ? `to ${usDate(view.ends_on)}` : "month to month"},{" "}
                {money(view.monthly_rent)} a month
              </p>
              <p>For {view.signer}, {view.role === "guarantor" ? "as guarantor" : "as a tenant"}</p>
              <ul className="flex flex-col gap-1 text-sm">
                {view.signers.map((s) => (
                  <li key={s.name} className="flex items-center gap-2">
                    {s.name}
                    <Badge tone={s.signed ? "positive" : "neutral"}>{s.signed ? "Signed" : "Not signed yet"}</Badge>
                  </li>
                ))}
              </ul>
            </section>
          </Card>
          <Notice tone="error">{error}</Notice>
          <Standing view={view} step={step} save={save} paper={paper} />
          {prompt}
        </>
      )}
    </div>
  );
}

type Step = (path: string, body?: Record<string, unknown>) => Promise<void>;
type Save = (path: string, name: string) => Promise<void>;

function Standing({ view, step, save, paper }: { view: SigningView; step: Step; save: Save; paper: () => void }) {
  if (view.status === "void") {
    return <Notice tone="info">{view.company} withdrew this lease offer. It takes no signature.</Notice>;
  }
  if (view.status === "expired") {
    return <Notice tone="info">This link has run out. Ask {view.company} for a new one.</Notice>;
  }
  if (view.status === "paper") {
    return (
      <Notice tone="info">
        You asked to sign on paper. {view.company} will give you the lease on paper; nothing more
        happens here.
      </Notice>
    );
  }
  if (view.status === "signed") {
    return (
      <Card title="Signed">
        <p className="text-base text-ink" data-testid="sign-status">
          You signed on {usDate((view.signed_at ?? "").slice(0, 10))}. Keep a copy of the lease and
          its signing certificate.
        </p>
        <div className="mt-4 flex flex-wrap gap-2">
          <Button type="button" variant="secondary" onClick={() => void save("document", "lease.pdf")}>
            Download the lease (PDF)
          </Button>
          <Button type="button" variant="secondary" onClick={() => void save("certificate", "signing-certificate.pdf")}>
            Download the signing certificate (PDF)
          </Button>
        </div>
      </Card>
    );
  }
  return view.consented ? (
    <Signing view={view} step={step} save={save} paper={paper} />
  ) : (
    <Consent view={view} step={step} paper={paper} />
  );
}

function Consent({ view, step, paper }: { view: SigningView; step: Step; paper: () => void }) {
  const [agreed, setAgreed] = useState(false);
  return (
    <Card title="Before you sign electronically">
      <Form className="flex flex-col gap-4" onSubmit={(event) => {
        event.preventDefault();
        return step("consent", { disclosure_sha256: view.disclosure_sha256 });
      }}>
        <div className="text-base whitespace-pre-line text-ink" data-testid="disclosure">{view.disclosure}</div>
        <label className="flex items-center gap-2 text-sm text-ink">
          <input type="checkbox" checked={agreed} onChange={(e) => setAgreed(e.target.checked)} />
          I have read this and agree to receive and sign this lease electronically
        </label>
        <div className="flex flex-wrap gap-2">
          <Button type="submit" disabled={!agreed}>Continue</Button>
          <Button type="button" variant="secondary" onClick={paper}>I'd rather sign on paper</Button>
        </div>
      </Form>
    </Card>
  );
}

function Signing({ view, step, save, paper }: { view: SigningView; step: Step; save: Save; paper: () => void }) {
  const [name, setName] = useState("");
  const [drawing, setDrawing] = useState<string | null>(null);
  const [intent, setIntent] = useState(false);
  return (
    <Card title="Read and sign">
      <Form className="flex flex-col gap-4" onSubmit={(event) => {
        event.preventDefault();
        return step("sign", { typed_name: name, document_sha256: view.document_sha256, drawing_base64: drawing });
      }}>
        <div className="flex flex-col gap-2">
          <div>
            <Button type="button" variant="secondary" onClick={() => void save("document", "lease.pdf")}>
              Open the lease (PDF)
            </Button>
          </div>
          <p className="text-sm text-ink-subtle">
            {view.opened ? "You opened the lease. " : "Open the lease and read it before you sign. "}
            Its SHA-256: <span className="font-mono break-all">{view.document_sha256}</span>
          </p>
        </div>
        <Field label={`Type your name as it is on the lease (${view.signer})`} id="sign-name" required
          maxLength={200} value={name} onChange={(e) => setName(e.target.value)} />
        <SignaturePad onChange={setDrawing} />
        <label className="flex items-center gap-2 text-sm text-ink">
          <input type="checkbox" checked={intent} onChange={(e) => setIntent(e.target.checked)} />
          I have read the lease and I sign it
        </label>
        <div className="flex flex-wrap gap-2">
          <Button type="submit" disabled={!view.opened || !intent}>Sign the lease</Button>
          <Button type="button" variant="secondary" onClick={paper}>I'd rather sign on paper</Button>
        </div>
      </Form>
    </Card>
  );
}
