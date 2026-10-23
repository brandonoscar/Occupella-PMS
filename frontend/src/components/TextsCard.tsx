import { useEffect, useState } from "react";
import { ApiError, api } from "../api";
import type { Keep } from "../latest";
import { keepAll, latest } from "../latest";
import { usDate } from "../money";
import type { PersonTexts, TextAbout } from "../texts";
import { TEXT_KIND, TEXT_STATUS, consentLine, shownPhone } from "../texts";
import { Badge } from "./Badge";
import { Button } from "./Button";
import { Field } from "./Field";
import { Form } from "./Form";
import { Notice } from "./Notice";
import { Said } from "./Said";
import { Select } from "./Select";
import { Card } from "./Shell";
import { SidePanel } from "./SidePanel";

// The longest text staff write; the company's name, theirs and how to stop are added to it.
const LONGEST = 300;

// A person's Texts tab (migration 0161): their word on being texted, which staff record as the
// person gave it to them; Write text while they agree and the server sends texts; and every text
// the PMS wrote them, newest first, with whether it went.
export function TextsCard({ about, aboutId, name }: { about: TextAbout; aboutId: string; name: string }) {
  const [view, setView] = useState<PersonTexts | null>(null);
  const [panel, setPanel] = useState<"word" | "write" | null>(null);
  const [said, setSaid] = useState("");
  const [error, setError] = useState("");
  const query = new URLSearchParams({ about, about_id: aboutId });

  function refresh(keep: Keep = keepAll) {
    api.get<PersonTexts>(`/api/texts?${query}`).then(keep(setView))
      .catch(keep((failed) => setError(failed instanceof ApiError ? failed.detail : String(failed))));
  }
  useEffect(() => {
    const run = latest();
    refresh(run.keep);
    return run.stop;
  }, [about, aboutId]);

  async function post<T>(path: string, body: unknown, done: (answer: T) => string) {
    setSaid("");
    try {
      const answer = await api.post<T>(path, body);
      setSaid(done(answer));
      refresh();
      setPanel(null);
      return "";
    } catch (failed) {
      return failed instanceof ApiError ? failed.detail : String(failed);
    }
  }

  const agreed = view?.consent?.consented === true;
  return (
    <Card title="Texts">
      <Said>{said}</Said>
      <Notice tone="error">{error}</Notice>
      {view === null ? (
        !error && <Notice tone="info">Loading</Notice>
      ) : (
        <>
          <p className="text-base" data-testid="text-consent">{consentLine(view.consent)}</p>
          {!view.sending && (
            <Notice tone="info">This server sends no texts yet: you can record their word, and texts start once it does.</Notice>
          )}
          <div className="mt-3 flex flex-wrap gap-2">
            <Button variant="secondary" onClick={() => setPanel("word")}>Record their word</Button>
            {agreed && view.sending && <Button variant="secondary" onClick={() => setPanel("write")}>Write text</Button>}
          </div>
          {view.texts.length === 0 ? (
            <p className="mt-3 text-sm text-ink-subtle">No text to {name} yet.</p>
          ) : (
            <ul aria-label={`Texts, ${name}`} className="mt-3 flex flex-col divide-y divide-line">
              {view.texts.map((line) => (
                <li key={line.id} className="flex flex-col gap-1 py-3">
                  <p className="flex flex-wrap items-center gap-2 text-base text-ink">
                    <span>{line.body}</span>
                    <Badge tone={TEXT_STATUS[line.status].tone}>{TEXT_STATUS[line.status].word}</Badge>
                  </p>
                  <p className="text-sm text-ink-subtle">
                    {TEXT_KIND[line.kind]} to {shownPhone(line.to)} · {line.by ?? "Sent by the PMS"} · {usDate(line.at)}
                  </p>
                  {line.status === "failed" && line.error && <p className="text-sm text-danger">{line.error}</p>}
                </li>
              ))}
            </ul>
          )}
          {panel === "word" && (
            <SidePanel title={`${name}'s word on texts`} onClose={() => setPanel(null)}>
              <WordForm start={view.consent?.phone ?? view.phone_on_file ?? ""} save={(body) =>
                post("/api/texts/consent", { about, about_id: aboutId, ...body }, () =>
                  body.consented ? `${name} agreed to texts.` : `${name} said no to texts.`)} />
            </SidePanel>
          )}
          {panel === "write" && view.consent && (
            <SidePanel title={`Text ${name}`} onClose={() => setPanel(null)}>
              <WriteForm to={view.consent.phone} send={(text) =>
                post<{ to: string }>("/api/texts", { about, about_id: aboutId, text }, (sent) =>
                  `Text sent to ${shownPhone(sent.to)}.`)} />
            </SidePanel>
          )}
        </>
      )}
    </Card>
  );
}

function WordForm({ start, save }: {
  start: string;
  save: (body: { phone: string; consented: boolean; how: "in writing" | "verbally" }) => Promise<string>;
}) {
  const [phone, setPhone] = useState(start ? shownPhone(start) : "");
  const [consented, setConsented] = useState("yes");
  const [how, setHow] = useState<"in writing" | "verbally">("in writing");
  const [error, setError] = useState("");
  return (
    <Form className="flex flex-col gap-3" onSubmit={async (event) => {
      event.preventDefault();
      setError(await save({ phone: phone.trim(), consented: consented === "yes", how }));
    }}>
      <p className="text-sm text-ink-muted">
        Record only what they told you themselves. Texts go to this number, and they can reply STOP at any time.
      </p>
      <Field label="Mobile number" id="text-phone" inputMode="tel" required maxLength={30} value={phone}
        onChange={(event) => setPhone(event.target.value)} />
      <Select label="Their word" id="text-word" value={consented} onChange={(event) => setConsented(event.target.value)}>
        <option value="yes">Yes, text me</option>
        <option value="no">No texts</option>
      </Select>
      <Select label="How they gave it" id="text-how" value={how}
        onChange={(event) => setHow(event.target.value as "in writing" | "verbally")}>
        <option value="in writing">In writing</option>
        <option value="verbally">By word of mouth</option>
      </Select>
      <Notice tone="error">{error}</Notice>
      <div><Button type="submit">Save</Button></div>
    </Form>
  );
}

function WriteForm({ to, send }: { to: string; send: (text: string) => Promise<string> }) {
  const [text, setText] = useState("");
  const [error, setError] = useState("");
  return (
    <Form className="flex flex-col gap-3" onSubmit={async (event) => {
      event.preventDefault();
      setError(await send(text.trim()));
    }}>
      <p className="text-sm text-ink-muted">{`To ${shownPhone(to)}. The company's name comes first; your name and "Reply STOP to stop." are added after it.`}</p>
      <label htmlFor="text-body" className="text-sm text-ink-muted">Text</label>
      <textarea id="text-body" required maxLength={LONGEST} rows={4} value={text}
        onChange={(event) => setText(event.target.value)}
        className="rounded-sm border border-line-strong bg-sunken px-2 py-1.5 text-base text-ink" />
      <p className="text-sm text-ink-subtle" aria-live="polite">{`${LONGEST - text.length} characters left`}</p>
      <Notice tone="error">{error}</Notice>
      <div><Button type="submit">Send text</Button></div>
    </Form>
  );
}
