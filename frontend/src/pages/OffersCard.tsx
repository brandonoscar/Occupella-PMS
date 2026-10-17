import { useEffect, useState } from "react";
import { ApiError, api } from "../api";
import { Button, LinkButton } from "../components/Button";
import { Field } from "../components/Field";
import { Notice } from "../components/Notice";
import { Card } from "../components/Shell";
import type { Application } from "../leasing";
import type { Paged } from "../rentals";
import { when } from "../maintenance";
import { money, usDate } from "../money";
import type { Offer, Offered, RenewableLease, SigningLink } from "../offers";
import {
  MAX_DOCUMENT,
  base64Of,
  dayAfter,
  isPdf,
  offerStatus,
  parseSigners,
  signersText,
  term,
} from "../offers";
import { Said } from "../components/Said";
import { Form } from "../components/Form";

const SELECT = "h-9 rounded-sm border border-line-strong bg-sunken px-2 text-sm text-ink";

// Staff's lease offers: a lease for an approved application or the renewal of a lease, with the
// PMC's own lease document (a PDF), sent to its signers through the server's e-sign partner.
// Staff send each signer their link, check who has signed, and make the offer the lease once
// everyone has. `application` names an approved application to start the offer from.
export function OffersCard({ application: chosen = "" }: { application?: string }) {
  const [approved, setApproved] = useState<Application[]>([]);
  const [leases, setLeases] = useState<RenewableLease[]>([]);
  const [offers, setOffers] = useState<Offer[] | null>(null);
  const [target, setTarget] = useState("");
  const [startsOn, setStartsOn] = useState("");
  const [endsOn, setEndsOn] = useState("");
  const [rent, setRent] = useState("");
  const [title, setTitle] = useState("");
  const [signers, setSigners] = useState("");
  const [document, setDocument] = useState("");
  const [shown, setShown] = useState<{ offer: string; links: SigningLink[] } | null>(null);
  const [done, setDone] = useState("");
  const [error, setError] = useState("");

  function show(failed: unknown) {
    setError(failed instanceof ApiError ? failed.detail : String(failed));
  }

  function refresh() {
    api
      .get<Paged<Application>>("/api/leasing/applications?show=open&per_page=200")
      .then((open) => setApproved(open.items.filter((a) => a.status === "approved" || a.status === "conditional")))
      .catch(show);
    api.get<RenewableLease[]>("/api/leasing/leases").then(setLeases).catch(show);
    api.get<Offer[]>("/api/leasing/offers?show=all").then(setOffers).catch(show);
  }

  useEffect(refresh, []);
  // Started from an application's Create draft lease: that application, once it is loaded.
  const [started, setStarted] = useState(false);
  useEffect(() => {
    if (started || !chosen || !approved.some((a) => a.id === chosen)) return;
    setStarted(true);
    choose(`application:${chosen}`);
  }, [approved, chosen, started]);

  async function act(work: () => Promise<unknown>) {
    setError("");
    setDone("");
    try {
      await work();
      refresh();
    } catch (failed) {
      show(failed);
    }
  }

  // An application that is a lease already takes no new offer.
  const leased = new Set((offers ?? []).filter((o) => o.lease_id).map((o) => o.application_id));
  const open = (offers ?? []).filter((o) => !o.lease_id);

  function choose(value: string) {
    setTarget(value);
    const [kind, id] = value.split(":");
    const application = approved.find((a) => kind === "application" && a.id === id);
    const lease = leases.find((l) => kind === "lease" && l.lease_id === id);
    if (application) {
      setSigners(signersText(application.applicants));
      setRent(application.monthly_rent);
      setStartsOn(application.move_in_on);
      setTitle("Lease");
    } else if (lease) {
      setSigners(signersText(lease.tenants));
      setRent(lease.monthly_rent);
      setStartsOn(lease.ends_on ? dayAfter(lease.ends_on) : "");
      setTitle("Lease renewal");
    }
    setEndsOn("");
  }

  async function pick(file: File | undefined) {
    setDocument("");
    if (!file) return;
    const bytes = new Uint8Array(await file.arrayBuffer());
    if (!isPdf(bytes)) return setError("the lease document is a PDF");
    if (bytes.length > MAX_DOCUMENT) return setError("the lease document is at most 5 MB");
    setError("");
    setDocument(base64Of(bytes));
  }

  function offer(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = event.currentTarget;
    const people = parseSigners(signers);
    if ("error" in people) return setError(people.error);
    const [kind, id] = target.split(":");
    const path =
      kind === "application"
        ? `/api/leasing/applications/${id}/offer`
        : `/api/leasing/leases/${id}/renewal`;
    return act(async () => {
      const made = await api.post<Offered>(path, {
        starts_on: startsOn,
        ends_on: endsOn || null,
        monthly_rent: rent,
        document_title: title,
        document_base64: document,
        signers: people.signers,
      });
      setShown({ offer: made.offer.id, links: made.links });
      setTarget("");
      setDocument("");
      form.reset();
    });
  }

  function complete(offer: Offer) {
    return act(async () => {
      const lease = await api.post<Offer>(`/api/leasing/offers/${offer.id}/complete`);
      setDone(`${lease.property}, ${lease.unit} has its lease from ${usDate(lease.starts_on)}.`);
    });
  }

  return (
    <Card title="Lease offers">
      <Notice tone="error">{error}</Notice>
      <Said>{done}</Said>
      <Form className="mb-8 flex flex-col gap-3" onSubmit={offer}>
        <label className="flex flex-col gap-1.5 text-sm text-ink-muted">
          Offer
          <select className={SELECT} required value={target} onChange={(e) => choose(e.target.value)}>
            <option value="">Choose an application or a lease</option>
            <optgroup label="A lease for an approved application">
              {approved
                .filter((a) => !leased.has(a.id))
                .map((a) => (
                  <option key={a.id} value={`application:${a.id}`}>
                    {a.applicants.map((p) => p.name).join(", ")} for {a.property}, {a.unit}
                  </option>
                ))}
            </optgroup>
            <optgroup label="The renewal of a lease">
              {leases.map((l) => (
                <option key={l.lease_id} value={`lease:${l.lease_id}`}>
                  {l.property}, {l.unit}: {l.tenants.map((t) => t.name).join(", ")}
                </option>
              ))}
            </optgroup>
          </select>
        </label>
        {target && (
          <>
            <div className="flex flex-wrap items-end gap-3">
              <Field label="Starts" id="offer-starts" type="date" required value={startsOn}
                onChange={(e) => setStartsOn(e.target.value)} />
              <Field label="Ends (empty for month to month)" id="offer-ends" type="date"
                value={endsOn} onChange={(e) => setEndsOn(e.target.value)} />
              <Field label="Rent a month" id="offer-rent" inputMode="decimal" required value={rent}
                onChange={(e) => setRent(e.target.value)} />
            </div>
            <Field label="Document title" id="offer-title" required maxLength={200} value={title}
              onChange={(e) => setTitle(e.target.value)} />
            <Field label="Lease document (PDF, your own)" id="offer-document" type="file"
              accept="application/pdf" onChange={(e) => pick(e.target.files?.[0])} />
            <label htmlFor="offer-signers" className="flex flex-col gap-1.5 text-sm text-ink-muted">
              Who signs, one a line: Name, email
              <textarea id="offer-signers" rows={3} required value={signers}
                onChange={(e) => setSigners(e.target.value)}
                className="rounded-sm border border-line-strong bg-sunken px-3 py-2 text-base text-ink" />
            </label>
            <div className="flex items-center gap-4">
              <Button type="submit" disabled={!document}>
                Send for signing
              </Button>
              {!document && <Notice tone="info">Choose the lease document first.</Notice>}
            </div>
          </>
        )}
      </Form>

      <h3 className="mb-3 text-md font-medium">Waiting to be the lease</h3>
      {offers === null ? (
        <Notice tone="info">Loading</Notice>
      ) : open.length === 0 ? (
        <Notice tone="info">No offers are waiting.</Notice>
      ) : (
        <ul className="flex flex-col divide-y divide-line">
          {open.map((o) => (
            <li key={o.id} className="flex flex-col gap-2 py-4" aria-label={`Offer for ${o.unit}`}>
              <p>
                {o.kind === "renewal" ? "Renewal" : "New lease"} at {o.property}, {o.unit}:{" "}
                {term(o.starts_on, o.ends_on)}, {money(o.monthly_rent)} a month.
              </p>
              <ul className="text-sm text-ink-muted">
                {o.signers.map((s) => (
                  <li key={s.position}>
                    {s.name} ({s.email}): {s.signed_at ? `signed ${when(s.signed_at)}` : "not signed yet"}
                  </li>
                ))}
              </ul>
              <p className="text-sm text-ink-subtle">
                {o.document_title}, offered {when(o.offered_at)}. {offerStatus(o)}
              </p>
              <div className="flex flex-wrap gap-2">
                <Button variant="secondary"
                  onClick={() => act(async () =>
                    setShown({ offer: o.id, links: await api.get<SigningLink[]>(`/api/leasing/offers/${o.id}/links`) }))}>
                  Signing links
                </Button>
                <Button variant="secondary"
                  onClick={() => act(() => api.post(`/api/leasing/offers/${o.id}/signatures`))}>
                  Check signatures
                </Button>
                <Button onClick={() => complete(o)}>Make it the lease</Button>
                <LinkButton href={`/api/leasing/offers/${encodeURIComponent(o.id)}/document`}>
                  Lease (PDF)
                </LinkButton>
              </div>
              {shown?.offer === o.id && (
                <div className="flex flex-col gap-1 rounded-sm border border-line p-3 text-sm">
                  <p className="text-ink-muted">
                    Send each signer their own link. Whoever has a link can sign as that signer.
                  </p>
                  {shown.links.map((link) => (
                    <p key={link.position}>
                      {link.name} ({link.email}):{" "}
                      <span className="font-mono break-all text-ink" data-testid="signing-link">
                        {link.url}
                      </span>
                    </p>
                  ))}
                </div>
              )}
            </li>
          ))}
        </ul>
      )}
    </Card>
  );
}
