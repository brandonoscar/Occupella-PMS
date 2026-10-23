import { useEffect, useState } from "react";
import { ApiError, api } from "../api";
import { Button } from "../components/Button";
import { Field } from "../components/Field";
import { Form } from "../components/Form";
import { Notice } from "../components/Notice";
import { Said } from "../components/Said";
import { Card } from "../components/Shell";
import type { MyTexts } from "../texts";
import { consentLine, shownPhone } from "../texts";

// A tenant's own word on texts from the company (migration 0161): yes at a mobile number, or no. A
// reply of STOP to any text says no too, and START says yes again.
export function TextMeCard() {
  const [view, setView] = useState<MyTexts | null>(null);
  const [phone, setPhone] = useState("");
  const [said, setSaid] = useState("");
  const [error, setError] = useState("");
  useEffect(() => {
    api.get<MyTexts>("/api/tenant/texts").then((found) => {
      setView(found);
      const start = found.consent?.phone ?? found.phone_on_file;
      setPhone(start ? shownPhone(start) : "");
    }).catch((failed) => setError(failed instanceof ApiError ? failed.detail : String(failed)));
  }, []);

  async function say(consented: boolean) {
    setSaid("");
    setError("");
    try {
      const answer = await api.post<MyTexts>("/api/tenant/texts", { phone: phone.trim(), consented });
      setView(answer);
      setSaid(consented ? `We'll text you at ${phone.trim()}.` : "No more texts.");
    } catch (failed) {
      setError(failed instanceof ApiError ? failed.detail : String(failed));
    }
  }

  return (
    <Card title="Texts">
      <Said>{said}</Said>
      {view === null ? (
        <Notice tone={error ? "error" : "info"}>{error || "Loading"}</Notice>
      ) : (
        <>
          <p className="text-base" data-testid="my-text-consent">
            {view.consent ? consentLine(view.consent).replace(/ \(recorded by .*\)$/, "") : "You haven't said whether we may text you."}
          </p>
          <p className="mt-1 text-sm text-ink-muted">
            {view.sending
              ? "Texts about your rent and your home, from the company. Message and data rates may apply. Reply STOP to any text to stop."
              : "The company doesn't send texts yet; your word is kept for when it does."}
          </p>
          <Form className="mt-3 flex flex-col gap-3" onSubmit={(event) => {
            event.preventDefault();
            return say(true);
          }}>
            <Field label="Mobile number" id="my-text-phone" inputMode="tel" required maxLength={30} value={phone}
              onChange={(event) => setPhone(event.target.value)} />
            <Notice tone="error">{error}</Notice>
            <div className="flex flex-wrap gap-2">
              <Button type="submit">Text me at this number</Button>
              {view.consent?.consented && (
                <Button type="button" variant="secondary" onClick={() => say(false)}>Stop texting me</Button>
              )}
            </div>
          </Form>
        </>
      )}
    </Card>
  );
}
