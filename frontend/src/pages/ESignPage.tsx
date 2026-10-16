import { useEffect, useState } from "react";
import { ApiError, api } from "../api";
import { Button } from "../components/Button";
import { Field } from "../components/Field";
import { Notice } from "../components/Notice";
import { Card } from "../components/Shell";
import { Form } from "../components/Form";

type Envelope = {
  partner: string;
  document_title: string;
  signer: string;
  document_sha256: string;
  signed: boolean;
};

// The fake e-sign partner's signing page, /esign#<token> (src/pms/api/fake_esign.py): served
// only while the server runs the fake, to try leasing out without a real partner. A real
// partner hosts its own page. Typing a name here is not a signature, and the page says so.
export function ESignPage() {
  const token = window.location.hash.slice(1);
  const [envelope, setEnvelope] = useState<Envelope | null>(null);
  const [name, setName] = useState("");
  const [error, setError] = useState("");

  useEffect(() => {
    api
      .post<Envelope>("/api/fake-esign/view", { token })
      .then(setEnvelope)
      .catch((failed) =>
        setError(
          failed instanceof ApiError && failed.status !== 404 && failed.status !== 422
            ? failed.detail
            : "This signing link isn't one the partner knows.",
        ),
      );
  }, [token]);

  async function sign(event: React.FormEvent) {
    event.preventDefault();
    setError("");
    try {
      await api.post("/api/fake-esign/sign", { token, name });
      setEnvelope((seen) => seen && { ...seen, signed: true });
    } catch (failed) {
      setError(failed instanceof ApiError ? failed.detail : String(failed));
    }
  }

  return (
    <div className="mx-auto flex max-w-2xl flex-col gap-6 px-6 py-10">
      <p className="text-sm text-ink-muted">
        {envelope?.partner ?? "E-sign partner"}: a stand-in for trying the PMS out. Typing your
        name here is not a real signature.
      </p>
      {envelope === null ? (
        <Notice tone={error ? "error" : "info"}>{error || "Loading"}</Notice>
      ) : (
        <Card title={envelope.document_title}>
          <p>For {envelope.signer}</p>
          <p className="mt-2 text-sm text-ink-subtle">
            Document SHA-256: <span className="font-mono break-all">{envelope.document_sha256}</span>
          </p>
          {envelope.signed ? (
            <p className="mt-4 text-md" data-testid="esign-status">
              Signed. You can close this page.
            </p>
          ) : (
            <Form className="mt-4 flex flex-col gap-3" onSubmit={sign}>
              <Field label="Type your name as it is on the lease" id="esign-name" required
                maxLength={200} value={name} onChange={(e) => setName(e.target.value)} />
              <Notice tone="error">{error}</Notice>
              <div>
                <Button type="submit">Sign</Button>
              </div>
            </Form>
          )}
        </Card>
      )}
    </div>
  );
}
