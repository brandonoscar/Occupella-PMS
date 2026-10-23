import { useEffect, useState } from "react";
import type { Me } from "../api";
import { ApiError, api } from "../api";
import { Button } from "../components/Button";
import { Field } from "../components/Field";
import { Form } from "../components/Form";
import { Notice } from "../components/Notice";
import { Said } from "../components/Said";
import { Card, PageHeader, Shell } from "../components/Shell";
import type { FeedSettings } from "../leasing";
import { feedPhone } from "../leasing";
import { STAFF_NAV } from "./staffNav";
import { useRecord } from "./useRecord";

// Settings → Listing feeds (migration 0250): the URL of each feed to give its listing site, the
// leasing contact every listing in them names, and what they still need. Staff choose which
// listings go in on Leasing, Listings. Signing up with each site is the company's own step;
// the PMS only publishes the feeds and sends nothing.
export function ListingFeedsPage({ me }: { me: Me }) {
  const [settings, setSettings] = useState<FeedSettings | null>(null);
  const [error, setError] = useState("");
  const [said, setSaid] = useState("");
  const [runs, setRuns] = useState(0);
  const { error: refused, record } = useRecord(() => setRuns((n) => n + 1), setSaid);
  const [name, setName] = useState("");
  const [email, setEmail] = useState("");
  const [phone, setPhone] = useState("");

  useEffect(() => {
    api.get<FeedSettings>("/api/leasing/feeds")
      .then((found) => {
        setSettings(found);
        setName(found.contact?.contact_name ?? "");
        setEmail(found.contact?.email ?? "");
        setPhone(found.contact ? feedPhone(found.contact.phone) : "");
      })
      .catch((why) => setError(why instanceof ApiError ? why.detail : String(why)));
  }, [runs]);

  async function copy(url: string, takes: string) {
    try {
      await navigator.clipboard.writeText(url);
      setSaid(`Copied the feed for ${takes}.`);
    } catch {
      setSaid(`The feed for ${takes}: ${url}`);
    }
  }

  return (
    <Shell me={me} nav={STAFF_NAV}>
      <PageHeader title="Listing feeds" />
      <Said>{said}</Said>
      {settings === null ? (
        <Notice tone={error ? "error" : "info"}>{error || "Loading"}</Notice>
      ) : (
        <>
          <Card title="Feeds">
            <p className="mb-3 text-base text-ink-muted">
              Give each site its feed&apos;s address when you sign up with it; the site fetches it on
              its own schedule. A listing goes in once you send it from Leasing, Listings, and
              leaves when you stop sending it or take it off the public page. Now in the
              feeds: {settings.listings === 1 ? "1 listing" : `${settings.listings} listings`}.
            </p>
            <p className="mb-3 text-base text-ink-muted">
              Each listing goes at the rent it asks. Where a law asks for the total of every
              mandatory monthly charge (Colorado&apos;s does), list the unit at that total.
            </p>
            {settings.missing.length > 0 && (
              <Notice tone="info">
                {`The feeds list nothing until the company has ${settings.missing.join(" and ")}.`}
              </Notice>
            )}
            <ul aria-label="Feeds" className="mt-3 divide-y divide-line">
              {settings.feeds.map((feed) => (
                <li key={feed.site} aria-label={feed.takes} className="flex flex-col gap-1 py-3">
                  <span className="text-base text-ink">{feed.takes}</span>
                  <div className="flex flex-wrap items-center gap-2">
                    <a href={feed.url} className="font-mono text-sm break-all text-iris hover:underline">{feed.url}</a>
                    <Button variant="secondary" size="sm" aria-label={`Copy the feed for ${feed.takes}`}
                      onClick={() => void copy(feed.url, feed.takes)}>
                      Copy
                    </Button>
                  </div>
                </li>
              ))}
            </ul>
          </Card>
          <Card title="Leasing contact">
            <p className="mb-3 text-base text-ink-muted">
              Every listing in the feeds names this contact for renters; the sites ask an email
              and a phone.
            </p>
            <Form className="flex flex-col gap-3" onSubmit={(event) => {
              event.preventDefault();
              void record("/api/leasing/feeds", {
                contact_name: name.trim() || null, email: email.trim(), phone,
              }, () => "Saved the leasing contact.");
            }}>
              <Field label="Name (optional)" id="feed-name" value={name} maxLength={100}
                onChange={(e) => setName(e.target.value)} />
              <Field label="Email" id="feed-email" type="email" required value={email}
                onChange={(e) => setEmail(e.target.value)} />
              <Field label="Phone" id="feed-phone" type="tel" required value={phone}
                onChange={(e) => setPhone(e.target.value)} />
              <div><Button type="submit">Save</Button></div>
              <Notice tone="error">{refused}</Notice>
            </Form>
          </Card>
        </>
      )}
    </Shell>
  );
}
