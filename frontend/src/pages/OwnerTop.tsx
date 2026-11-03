import { useEffect, useState } from "react";
import { api } from "../api";
import { PhoneLink } from "../components/PhoneLink";
import type { Owner } from "../directory";
import { addressLines } from "../directory";
import { latest } from "../latest";
import { money, sumMoney, usDate } from "../money";
import type { DrawDay, Payee } from "../payouts";
import { bankLine, DRAW_DAY, methodWord } from "../payouts";
import type { OnFile } from "../taxIds";
import { taxIdLine } from "../taxIds";

const LINK = "font-medium text-iris hover:text-iris-hover hover:underline";

// The next draw day on or after today ("YYYY-MM-DD"): this month's, or next month's once it has
// passed (a draw day is 1 to 28, so every month has it).
export function nextDrawOn(drawDay: number, today: string): string {
  const [year, month, day] = today.split("-").map(Number) as [number, number, number];
  const at = day <= drawDay ? new Date(Date.UTC(year, month - 1, drawDay)) : new Date(Date.UTC(year, month, drawDay));
  return at.toISOString().slice(0, 10);
}

// What a draw of every one of the owner's accounts would pay now: what each has available.
export function drawTotal(owner: Pick<Owner, "accounts">): string {
  return sumMoney(owner.accounts.map((a) => (Number(a.available) > 0 ? a.available : "0")));
}

// The top of an owner's page (ADR 0010 phase 3, part J): how to reach them, how they are paid,
// their tax ID's last four, what their accounts hold and the next draw.
export function OwnerTop({ owner, today }: { owner: Owner; today: string }) {
  const [payee, setPayee] = useState<Payee | null>(null);
  const [taxId, setTaxId] = useState<OnFile | null>(null);
  const [drawDay, setDrawDay] = useState<DrawDay | null>(null);
  useEffect(() => {
    const run = latest();
    api.get<Payee>(`/api/payouts/owners/${owner.id}`).then(run.keep(setPayee)).catch(() => undefined);
    api.get<OnFile>(`/api/tax-ids/owners/${owner.id}`).then(run.keep(setTaxId)).catch(() => undefined);
    api.get<DrawDay>(DRAW_DAY).then(run.keep(setDrawDay)).catch(() => undefined);
    return run.stop;
  }, [owner.id]);
  const address = addressLines(owner.contact);
  const balance = sumMoney(owner.accounts.map((a) => a.balance));
  return (
    <dl aria-label={`About ${owner.name}`} data-testid="owner-top"
      className="grid grid-cols-[max-content_1fr] gap-x-6 gap-y-1 text-base sm:grid-cols-[max-content_1fr_max-content_1fr]">
      <dt className="text-ink-muted">Phone</dt>
      <dd>{owner.contact.phone ? <PhoneLink phone={owner.contact.phone} /> : "None on file"}</dd>
      <dt className="text-ink-muted">Balance</dt>
      <dd data-testid="owner-balance">{money(balance)}</dd>
      <dt className="text-ink-muted">Email</dt>
      <dd>{owner.contact.email ? <a className={LINK} href={`mailto:${owner.contact.email}`}>{owner.contact.email}</a> : "None on file"}</dd>
      <dt className="text-ink-muted">Next draw</dt>
      <dd data-testid="owner-next-draw">
        {drawDay === null ? "" : !drawDay.draw_day
          ? "No draw day set"
          : `${usDate(nextDrawOn(drawDay.draw_day, today))}, ${money(drawTotal(owner))} available now`}
      </dd>
      <dt className="text-ink-muted">Mailing address</dt>
      <dd>{address.length ? address.map((line) => <span key={line} className="block">{line}</span>) : "None on file"}</dd>
      <dt className="text-ink-muted">Paid by</dt>
      <dd data-testid="owner-paid-by">
        {payee === null ? "" : payee.bank && payee.method === "EFT"
          ? `ACH to ${bankLine(payee.bank)}`
          : methodWord(payee.method ?? "check")}
      </dd>
      <dt className="text-ink-muted">Tax ID</dt>
      <dd data-testid="owner-tax-id">{taxId === null ? "" : taxId.tax_id ? taxIdLine(taxId.tax_id) : "None on file"}</dd>
    </dl>
  );
}
