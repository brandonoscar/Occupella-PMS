import { useEffect, useState } from "react";
import type { Me } from "../api";
import { ApiError, api } from "../api";
import { Badge } from "../components/Badge";
import { Button, LinkButton } from "../components/Button";
import { Form } from "../components/Form";
import { Notice } from "../components/Notice";
import { RowMenu } from "../components/RowMenu";
import { Said } from "../components/Said";
import { Card, PageHeader, Shell } from "../components/Shell";
import { SidePanel } from "../components/SidePanel";
import { Wide } from "../components/Wide";
import { when } from "../maintenance";
import { money, usDate } from "../money";
import type { Offer, Offered, SigningLink } from "../offers";
import { offerBadge, sentLine, signedCount, term } from "../offers";
import { GoLink } from "./NeedsAttention";
import { STAFF_NAV } from "./staffNav";
import type { Recorder } from "./useRecord";
import { useRecord } from "./useRecord";

const LINK = "font-medium text-iris hover:text-iris-hover hover:underline";
const TEXTAREA = "rounded-sm border border-line-strong bg-sunken px-3 py-2 text-base text-ink";

// A draft lease's own page (ADR 0006): its terms, the deposit it asks and its monthly charges, the
// company's lease PDF, its signers and who has signed. Send sends a draft to its signers through
// the e-sign partner; Resend shows each signer's link again; Check signatures records what the
// partner reports; Activate lease makes a signed offer the lease, with its monthly charges. Its ⋯
// menu voids an offer that is not a lease yet.
export function DraftLeasePage({ me, id }: { me: Me; id: string }) {
  const [found, setFound] = useState<Offer | null>(null);
  const [error, setError] = useState("");
  const [links, setLinks] = useState<SigningLink[] | null>(null);
  const [voiding, setVoiding] = useState(false);
  const [runs, setRuns] = useState(0);
  const { said, error: refused, record } = useRecord(() => setRuns((n) => n + 1));

  useEffect(() => {
    api.get<Offer>(`/api/leasing/offers/${id}`).then(setFound)
      .catch((why) => setError(why instanceof ApiError ? why.detail : String(why)));
  }, [id, runs]);

  const voidThenClose: Recorder = async (path, body, done) => {
    const recorded = await record(path, body, done);
    if (recorded) setVoiding(false);
    return recorded;
  };

  if (found === null) {
    return (
      <Shell me={me} nav={STAFF_NAV}>
        <PageHeader title="Draft lease" />
        <Notice tone={error ? "error" : "info"}>{error || "Loading"}</Notice>
      </Shell>
    );
  }
  const name = `${found.property}, ${found.unit}`;
  const badge = offerBadge(found.status);
  const waiting = found.status === "sent" || found.status === "partially signed";
  const resend = async () => {
    setLinks(null);
    try {
      setLinks(await api.get<SigningLink[]>(`/api/leasing/offers/${id}/links`));
    } catch (why) {
      setError(why instanceof ApiError ? why.detail : String(why));
    }
  };
  return (
    <Shell me={me} nav={STAFF_NAV}>
      <PageHeader title={`${found.kind === "renewal" ? "Renewal" : "Lease"} for ${name}`} actions={
        <div className="flex flex-wrap items-center gap-2">
          {found.status === "draft" && (
            <Button onClick={() => record<Offered>(`/api/leasing/offers/${id}/send`, {}, (sent) => {
              setLinks(sent.links);
              return sentLine(sent);
            })}>
              Send
            </Button>
          )}
          {waiting && (
            <>
              <Button variant="secondary" onClick={resend}>Resend</Button>
              <Button variant="secondary" onClick={() => record<Offer>(`/api/leasing/offers/${id}/signatures`, {},
                (checked) => `${signedCount(checked)}.`)}>
                Check signatures
              </Button>
            </>
          )}
          {(waiting || found.status === "signed") && (
            <Button onClick={() => record<Offer>(`/api/leasing/offers/${id}/complete`, {},
              (made) => `${name} has its lease from ${usDate(made.starts_on)}.`)}>
              Activate lease
            </Button>
          )}
          {found.lease_id && <LinkButton href={`/leases/${found.lease_id}`}>Open the lease</LinkButton>}
          {found.status !== "leased" && found.status !== "void" && (
            <RowMenu label={name} actions={[{ label: "Void", danger: true, onSelect: () => setVoiding(true) }]} />
          )}
        </div>
      } />
      <p className="flex flex-wrap items-center gap-3 text-base text-ink-muted">
        <GoLink href="/draft-leases" className={LINK}>Draft leases</GoLink>
        <Badge tone={badge.tone} data-testid="status">{badge.word}</Badge>
        {found.sent && <span>{signedCount(found)}</span>}
      </p>
      <Said>{said}</Said>
      {!voiding && <Notice tone="error">{refused || error}</Notice>}
      {links && (
        <Card title="Signing links">
          <ul className="flex flex-col gap-1 text-base" aria-label="Signing links">
            {links.map((link) => (
              <li key={link.position}>
                {link.name} ({link.email}):{" "}
                <span className="font-mono break-all text-ink" data-testid="signing-link">{link.url}</span>
              </li>
            ))}
          </ul>
        </Card>
      )}
      <Card title="Summary">
        <dl className="grid grid-cols-2 gap-x-6 gap-y-2 text-base md:grid-cols-3" aria-label="Summary">
          <Item label="Unit"><GoLink href={`/units/${found.unit_id}`} className={LINK}>{found.unit}</GoLink></Item>
          <Item label="Property"><GoLink href={`/properties/${found.property_id}`} className={LINK}>{found.property}</GoLink></Item>
          <Item label="Term">{term(found.starts_on, found.ends_on)}</Item>
          <Item label="Rent a month">{money(found.monthly_rent)}</Item>
          <Item label="Security deposit">{found.security_deposit ? money(found.security_deposit) : "None"}</Item>
          <Item label="Created">{when(found.offered_at)}</Item>
          {found.application_id && (
            <Item label="Application"><GoLink href={`/applicants/${found.application_id}`} className={LINK}>Open it</GoLink></Item>
          )}
          {found.renews_lease_id && (
            <Item label="Renews"><GoLink href={`/leases/${found.renews_lease_id}`} className={LINK}>The lease</GoLink></Item>
          )}
          <Item label="Document">
            <a className={LINK} href={`/api/leasing/offers/${encodeURIComponent(id)}/document`}>{found.document_title} (PDF)</a>
          </Item>
        </dl>
      </Card>
      <Card title="Monthly charges">
        {found.recurring_charges.length === 0 ? (
          <p className="text-base text-ink-muted">Rent only.</p>
        ) : (
          <Wide>
            <table className="w-full text-base" aria-label="Monthly charges">
              <thead>
                <tr>
                  <th className="text-left">For</th>
                  <th className="text-left">Kind</th>
                  <th className="text-right">Amount</th>
                </tr>
              </thead>
              <tbody>
                {found.recurring_charges.map((c, n) => (
                  <tr key={n}>
                    <td>{c.memo}</td>
                    <td>{c.kind === "fee" ? "Fee" : "Credit"}</td>
                    <td className="text-right">{money(c.kind === "credit" ? `-${c.amount}` : c.amount)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </Wide>
        )}
      </Card>
      <Card title="Signers">
        <Wide>
          <table className="w-full text-base" aria-label="Signers">
            <thead>
              <tr>
                <th className="text-left">Signer</th>
                <th className="text-left">Email</th>
                <th className="text-left">Signed</th>
              </tr>
            </thead>
            <tbody>
              {found.signers.map((s) => (
                <tr key={s.position}>
                  <td>{s.name}{s.role === "guarantor" && <> <Badge tone="neutral">Guarantor</Badge></>}</td>
                  <td>{s.email}</td>
                  <td>{s.signed_at ? when(s.signed_at) : <span className="text-ink-muted">{found.sent ? "Not yet" : "Not sent"}</span>}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </Wide>
      </Card>
      {found.void && (
        <Card title="Void">
          <p className="text-base" data-testid="void">
            {when(found.void.recorded_at)}
            {found.void.recorded_by ? `, by ${found.void.recorded_by}` : ""}
            {found.void.reason ? `: ${found.void.reason}` : "."}
          </p>
        </Card>
      )}
      {voiding && (
        <SidePanel title="Void" onClose={() => setVoiding(false)}>
          <p className="text-base text-ink">{name}, {term(found.starts_on, found.ends_on)}</p>
          <VoidForm offer={found} record={voidThenClose} />
          <Notice tone="error">{refused}</Notice>
        </SidePanel>
      )}
    </Shell>
  );
}

function Item({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div>
      <dt className="text-sm text-ink-muted">{label}</dt>
      <dd>{children}</dd>
    </div>
  );
}

// Voids the offer, and why: its signers can no longer sign it, and it is never made the lease.
function VoidForm({ offer, record }: { offer: Offer; record: Recorder }) {
  const [reason, setReason] = useState("");
  return (
    <Form className="flex flex-col gap-3" onSubmit={(event) => {
      event.preventDefault();
      return record(`/api/leasing/offers/${offer.id}/void`, { reason: reason.trim() || null },
        () => `Voided the ${offer.kind === "renewal" ? "renewal" : "lease"} for ${offer.property}, ${offer.unit}.`);
    }}>
      <label htmlFor="void-reason" className="flex flex-col gap-1.5 text-sm text-ink-muted">
        Why (optional)
        <textarea id="void-reason" rows={3} maxLength={500} value={reason}
          onChange={(e) => setReason(e.target.value)} className={TEXTAREA} />
      </label>
      <div><Button type="submit" variant="danger">Void it</Button></div>
    </Form>
  );
}
