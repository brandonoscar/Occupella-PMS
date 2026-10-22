import { useEffect, useState } from "react";
import { ApiError, api } from "../api";
import { Button } from "../components/Button";
import { Field } from "../components/Field";
import { Form } from "../components/Form";
import { Notice } from "../components/Notice";
import { Said } from "../components/Said";
import { Select } from "../components/Select";
import { SidePanel } from "../components/SidePanel";
import { day, usDate } from "../money";
import type { OwnerAccount as PropertyOwner } from "../rentals";
import { sharesLine, sharesTotal } from "../rentals";
import type { Portfolio } from "../setup";
import { useRecord } from "./useRecord";

// The setup forms, each in a side panel on the page of what it changes (ADR 0006, step 12:
// "everything else moves to where it's used"): a property from Properties; a unit, another owner,
// management terms and late and NSF fee terms from the property's page; a lease already in place
// from its unit's page; terms from the owner's page too. Each posts to /api/setup; on success it
// says what it did (a toast on the page), closes and refreshes the page.

export type Panel = {
  // What the page shows a save did, as a toast.
  say: (said: string) => void;
  refresh: () => void;
  close: () => void;
};

function useSave({ say, refresh, close }: Panel) {
  const { error, record } = useRecord(refresh, say);
  async function save<T>(path: string, body: unknown, done: (answer: T) => string): Promise<boolean> {
    const saved = await record<T>(`/api/setup/${path}`, body, done);
    if (saved) close();
    return saved;
  }
  return { error, save };
}

// The company's owners, bank accounts and tenants, for a panel's choices.
export function usePortfolio(): [Portfolio | null, string, () => void] {
  const [books, setBooks] = useState<Portfolio | null>(null);
  const [error, setError] = useState("");
  const load = () => {
    api.get<Portfolio>("/api/setup").then(setBooks)
      .catch((failed) => setError(failed instanceof ApiError ? failed.detail : String(failed)));
  };
  useEffect(load, []);
  return [books, error, load];
}

function Loading({ error }: { error: string }) {
  return <Notice tone={error ? "error" : "info"}>{error || "Loading"}</Notice>;
}

function OwnerAndBank({ books, owner, bank, setOwner, setBank, id }: {
  books: Portfolio;
  owner: string;
  bank: string;
  setOwner: (id: string) => void;
  setBank: (id: string) => void;
  id: string;
}) {
  const operating = books.bank_accounts.filter((b) => b.kind === "operating");
  return (
    <>
      <Select label="Owner" id={`${id}-owner`} required value={owner} onChange={(e) => setOwner(e.target.value)}>
        <option value="">Choose an owner</option>
        {books.owners.map((o) => <option key={o.id} value={o.id}>{o.name}</option>)}
      </Select>
      <Select label="Held in" id={`${id}-bank`} required value={bank || (operating.length === 1 ? operating[0]!.id : "")}
        onChange={(e) => setBank(e.target.value)}>
        <option value="">Choose an operating account</option>
        {operating.map((b) => <option key={b.id} value={b.id}>{b.name}</option>)}
      </Select>
    </>
  );
}

const firstOperating = (books: Portfolio, bank: string) => {
  const operating = books.bank_accounts.filter((b) => b.kind === "operating");
  return bank || (operating.length === 1 ? operating[0]!.id : "");
};

// Properties → Add property: its name, its first owner and the operating account their money is
// held in; then its page.
export function AddPropertyPanel({ panel, opened }: { panel: Panel; opened: (id: string) => void }) {
  const [books, unread] = usePortfolio();
  const { error, save } = useSave(panel);
  const [name, setName] = useState("");
  const [owner, setOwner] = useState("");
  const [bank, setBank] = useState("");
  return (
    <SidePanel title="Add property" onClose={panel.close}>
      {books === null ? <Loading error={unread} /> : (
        <Form className="flex flex-col gap-3" onSubmit={(event) => {
          event.preventDefault();
          return save<{ id: string }>("properties", { name, owner_id: owner, bank_account_id: firstOperating(books, bank) },
            (made) => { opened(made.id); return `Added ${name.trim()}.`; });
        }}>
          <Field label="Property name" id="property-name" required maxLength={200} value={name}
            onChange={(e) => setName(e.target.value)} />
          <OwnerAndBank books={books} owner={owner} bank={bank} setOwner={setOwner} setBank={setBank} id="property" />
          <div><Button type="submit">Add property</Button></div>
          <Notice tone="error">{error}</Notice>
        </Form>
      )}
    </SidePanel>
  );
}

export function AddUnitPanel({ panel, propertyId }: { panel: Panel; propertyId: string }) {
  const { error, save } = useSave(panel);
  const [name, setName] = useState("");
  return (
    <SidePanel title="Add unit" onClose={panel.close}>
      <Form className="flex flex-col gap-3" onSubmit={(event) => {
        event.preventDefault();
        return save("units", { property_id: propertyId, name }, () => `Added ${name.trim()}.`);
      }}>
        <Field label="Unit name" id="unit-name" required maxLength={200} value={name} onChange={(e) => setName(e.target.value)} />
        <div><Button type="submit">Add unit</Button></div>
        <Notice tone="error">{error}</Notice>
      </Form>
    </SidePanel>
  );
}

// Another owner of the property, their money held in an operating account.
export function AddOwnerPanel({ panel, propertyId }: { panel: Panel; propertyId: string }) {
  const [books, unread] = usePortfolio();
  const { error, save } = useSave(panel);
  const [owner, setOwner] = useState("");
  const [bank, setBank] = useState("");
  return (
    <SidePanel title="Add owner" onClose={panel.close}>
      {books === null ? <Loading error={unread} /> : (
        <Form className="flex flex-col gap-3" onSubmit={(event) => {
          event.preventDefault();
          const name = books.owners.find((o) => o.id === owner)?.name ?? "the owner";
          return save(`properties/${propertyId}/owners`, { owner_id: owner, bank_account_id: firstOperating(books, bank) },
            () => `Added ${name}'s account.`);
        }}>
          <OwnerAndBank books={books} owner={owner} bank={bank} setOwner={setOwner} setBank={setBank} id="add-owner" />
          <div><Button type="submit">Add owner</Button></div>
          <Notice tone="error">{error}</Notice>
        </Form>
      )}
    </SidePanel>
  );
}

// A property's ownership from a day (q044): each owner's percent, by their account at the
// property, together 100. Starts from the shares in force; a blank leaves an owner out.
export function OwnershipPanel({ panel, propertyId, name, owners }: {
  panel: Panel;
  propertyId: string;
  name: string;
  owners: PropertyOwner[];
}) {
  const { error, save } = useSave(panel);
  const [from, setFrom] = useState(day(new Date()));
  const [percents, setPercents] = useState<Record<string, string>>(
    Object.fromEntries(owners.map((o) => [o.account, o.percent ?? ""])));
  const total = sharesTotal(Object.values(percents));
  const whole = total.valid && total.parts === 1_000_000;
  const shares = owners.filter((o) => percents[o.account]!.trim() !== "")
    .map((o) => ({ account: o.account, owner: o.owner, percent: percents[o.account]!.trim() }));
  return (
    <SidePanel title="Set ownership" onClose={panel.close}>
      <Form className="flex flex-col gap-3" onSubmit={(event) => {
        event.preventDefault();
        if (!whole) return;
        return save(`properties/${propertyId}/ownership`, {
          starts_on: from, shares: shares.map(({ account, percent }) => ({ account, percent })),
        }, () => `Ownership of ${name} from ${usDate(from)}: ${sharesLine(shares)}.`);
      }}>
        <p className="text-sm text-ink-muted">
          From this day each payment, prepaid rent used and deposit kept at move-out goes to the
          owners by their percent, to the cent; earlier days keep the shares they had.
        </p>
        <Field label="In force from" id="ownership-from" type="date" required value={from}
          onChange={(e) => setFrom(e.target.value)} />
        {owners.map((o) => (
          <Field key={o.account} label={`${o.owner}, percent`} id={`ownership-${o.account}`} inputMode="decimal"
            value={percents[o.account]} onChange={(e) => setPercents({ ...percents, [o.account]: e.target.value })} />
        ))}
        <p className="text-sm text-ink-muted" data-testid="ownership-total">
          {!total.valid
            ? "A percent is above 0, at most 100, to four decimals."
            : whole ? "Together 100%." : `Together ${total.text}; the shares come to 100%.`}
        </p>
        <div><Button type="submit" disabled={!whole}>Set ownership</Button></div>
        <Notice tone="error">{error}</Notice>
      </Form>
    </SidePanel>
  );
}

const AMOUNTS = [
  ["fee_percent", "Fee, percent of rent and recurring fees collected"],
  ["minimum_fee", "Minimum fee"],
  ["flat_fee", "Flat fee"],
  ["leasing_fee_percent", "Leasing fee, percent of rent"],
  ["reserve", "Reserve"],
  ["approval_limit", "Owner approves bills over"],
  ["bill_markup_percent", "Markup on bills, percent"],
] as const;
type Amount = (typeof AMOUNTS)[number][0];

// Management terms of an owner's property account from a day: new terms are a new row from that
// day; earlier ones stay as they were. A property with several owners can take the same terms for
// every one of them (q044), so its management fee is one for the property, split by their shares.
const EVERY = "every-owner";
export function TermsPanel({ panel, accounts, everyOwnerOf }: {
  panel: Panel; accounts: { account: string; label: string }[]; everyOwnerOf?: string;
}) {
  const { error, save } = useSave(panel);
  const [account, setAccount] = useState(accounts[0]?.account ?? "");
  const [from, setFrom] = useState(day(new Date()));
  const [amounts, setAmounts] = useState<Record<Amount, string>>(
    Object.fromEntries(AMOUNTS.map(([key]) => [key, ""])) as Record<Amount, string>,
  );
  return (
    <SidePanel title="Set management terms" onClose={panel.close}>
      <Form className="flex flex-col gap-3" onSubmit={(event) => {
        event.preventDefault();
        const every = account === EVERY;
        const label = every ? `every owner of ${everyOwnerOf}` : (accounts.find((a) => a.account === account)?.label ?? "");
        return save("agreements", {
          account: every ? accounts[0]!.account : account, starts_on: from,
          ...(every ? { every_owner: true } : {}),
          ...Object.fromEntries(AMOUNTS.map(([key]) => [key, amounts[key].trim() || "0"])),
        }, () => `New terms for ${label} from ${from}.`);
      }}>
        <Select label="For" id="terms-account" required value={account} onChange={(e) => setAccount(e.target.value)}>
          {everyOwnerOf && <option value={EVERY}>Every owner of {everyOwnerOf} (one fee, split by their shares)</option>}
          {accounts.map((a) => <option key={a.account} value={a.account}>{a.label}</option>)}
        </Select>
        <Field label="In force from" id="terms-from" type="date" required value={from} onChange={(e) => setFrom(e.target.value)} />
        {AMOUNTS.map(([key, label]) => (
          <Field key={key} label={label} id={`terms-${key}`} inputMode="decimal" value={amounts[key]}
            onChange={(e) => setAmounts({ ...amounts, [key]: e.target.value })} />
        ))}
        <div><Button type="submit">Set terms</Button></div>
        <Notice tone="error">{error}</Notice>
      </Form>
    </SidePanel>
  );
}

export function LateFeesPanel({ panel, propertyId }: { panel: Panel; propertyId: string }) {
  const { error, save } = useSave(panel);
  const [fees, setFees] = useState({ starts_on: day(new Date()), grace_days: "5", flat_fee: "", percent: "", maximum: "" });
  const set = (key: keyof typeof fees) => (e: React.ChangeEvent<HTMLInputElement>) => setFees({ ...fees, [key]: e.target.value });
  return (
    <SidePanel title="Set late fees" onClose={panel.close}>
      <Form className="flex flex-col gap-3" onSubmit={(event) => {
        event.preventDefault();
        return save("late-fees", {
          property_id: propertyId, starts_on: fees.starts_on, grace_days: Number(fees.grace_days),
          flat_fee: fees.flat_fee.trim() || "0", percent: fees.percent.trim() || "0", maximum: fees.maximum.trim() || null,
        }, () => `New late fee terms from ${fees.starts_on}.`);
      }}>
        <Field label="Late fees from" id="fees-from" type="date" required value={fees.starts_on} onChange={set("starts_on")} />
        <Field label="Grace days" id="fees-grace" inputMode="numeric" required value={fees.grace_days} onChange={set("grace_days")} />
        <Field label="Flat fee" id="fees-flat" inputMode="decimal" value={fees.flat_fee} onChange={set("flat_fee")} />
        <Field label="Percent of unpaid" id="fees-percent" inputMode="decimal" value={fees.percent} onChange={set("percent")} />
        <Field label="Up to" id="fees-maximum" inputMode="decimal" value={fees.maximum} onChange={set("maximum")} />
        <div><Button type="submit">Set late fees</Button></div>
        <Notice tone="error">{error}</Notice>
      </Form>
    </SidePanel>
  );
}

export function NsfFeePanel({ panel, propertyId }: { panel: Panel; propertyId: string }) {
  const { error, save } = useSave(panel);
  const [from, setFrom] = useState(day(new Date()));
  const [fee, setFee] = useState("");
  return (
    <SidePanel title="Set NSF fee" onClose={panel.close}>
      <Form className="flex flex-col gap-3" onSubmit={(event) => {
        event.preventDefault();
        return save("nsf-fees", { property_id: propertyId, starts_on: from, fee: fee.trim() }, () => `New NSF fee from ${from}.`);
      }}>
        <Field label="NSF fee from" id="nsf-from" type="date" required value={from} onChange={(e) => setFrom(e.target.value)} />
        <Field label="NSF fee" id="nsf-fee" inputMode="decimal" required value={fee} onChange={(e) => setFee(e.target.value)} />
        <div><Button type="submit">Set NSF fee</Button></div>
        <Notice tone="error">{error}</Notice>
      </Form>
    </SidePanel>
  );
}

// The yearly rate a property's security deposits earn from a day, as the law the company follows
// sets it (migration 0122): the PMS builds in no state's rate; 0 earns none from that day.
export function DepositInterestPanel({ panel, propertyId }: { panel: Panel; propertyId: string }) {
  const { error, save } = useSave(panel);
  const [from, setFrom] = useState(day(new Date()));
  const [percent, setPercent] = useState("");
  return (
    <SidePanel title="Set deposit interest" onClose={panel.close}>
      <Form className="flex flex-col gap-3" onSubmit={(event) => {
        event.preventDefault();
        return save("deposit-interest",
          { property_id: propertyId, starts_on: from, annual_percent: percent.trim() },
          () => `Deposits earn ${percent.trim()}% a year from ${usDate(from)}.`);
      }}>
        <Field label="Deposits earn from" id="interest-from" type="date" required value={from}
          onChange={(e) => setFrom(e.target.value)} />
        <Field label="Percent a year" id="interest-percent" inputMode="decimal" required value={percent}
          onChange={(e) => setPercent(e.target.value)} />
        <p className="text-sm text-ink-muted">
          Each day&apos;s deposit earns the day&apos;s rate. Pay it from the lease&apos;s page, when the law you
          follow says to: as a credit against the rent, or added to the deposit.
        </p>
        <div><Button type="submit">Set deposit interest</Button></div>
        <Notice tone="error">{error}</Notice>
      </Form>
    </SidePanel>
  );
}

// The lease-break fee leases starting from a day break under: a flat fee and months of rent,
// charged only when staff end such a lease before its end date and tick it.
export function LeaseBreakFeePanel({ panel, propertyId }: { panel: Panel; propertyId: string }) {
  const { error, save } = useSave(panel);
  const [from, setFrom] = useState(day(new Date()));
  const [flat, setFlat] = useState("0.00");
  const [months, setMonths] = useState("0");
  return (
    <SidePanel title="Set lease-break fee" onClose={panel.close}>
      <Form className="flex flex-col gap-3" onSubmit={(event) => {
        event.preventDefault();
        return save("lease-break-fees",
          { property_id: propertyId, starts_on: from, flat_fee: flat.trim(), rent_months: months.trim() },
          () => `New lease-break fee for leases starting from ${from}.`);
      }}>
        <Field label="For leases starting from" id="break-from" type="date" required value={from}
          onChange={(e) => setFrom(e.target.value)} />
        <Field label="Flat fee" id="break-flat" inputMode="decimal" required value={flat}
          onChange={(e) => setFlat(e.target.value)} />
        <Field label="Months of rent" id="break-months" inputMode="decimal" required value={months}
          onChange={(e) => setMonths(e.target.value)} />
        <p className="text-sm text-ink-muted">
          Charged only when you end a lease before its end date and choose to charge it.
        </p>
        <div><Button type="submit">Set lease-break fee</Button></div>
        <Notice tone="error">{error}</Notice>
      </Form>
    </SidePanel>
  );
}

// A lease already in place on the unit (a new tenant's lease comes through Leasing, Draft
// leases): its days, rent and tenants, who may be added here first.
export function AddLeasePanel({ panel, unitId, propertyId, propertyName }: {
  panel: Panel;
  unitId: string;
  propertyId: string;
  propertyName: string;
}) {
  const [books, unread, reload] = usePortfolio();
  const { error, save } = useSave(panel);
  const adding = useRecord(reload);
  const [lease, setLease] = useState({ starts_on: "", ends_on: "", rent: "", due: "1" });
  const [chosen, setChosen] = useState<string[]>([]);
  const [tenant, setTenant] = useState("");
  const tenants = books?.tenants.filter((t) => t.property_id === propertyId) ?? [];
  const toggle = (id: string, on: boolean) => setChosen((now) => (on ? [...now, id] : now.filter((t) => t !== id)));
  return (
    <SidePanel title="Add lease" onClose={panel.close}>
      {books === null ? <Loading error={unread} /> : (
        <Form className="flex flex-col gap-3" onSubmit={(event) => {
          event.preventDefault();
          return save("leases", {
            unit_id: unitId, starts_on: lease.starts_on, ends_on: lease.ends_on || null,
            monthly_rent: lease.rent.trim(), tenant_ids: chosen, rent_due_day: Number(lease.due),
          }, () => "Added the lease.");
        }}>
          <Field label="Starts" id="lease-starts" type="date" required value={lease.starts_on}
            onChange={(e) => setLease({ ...lease, starts_on: e.target.value })} />
          <Field label="Ends (empty for month to month)" id="lease-ends" type="date" value={lease.ends_on}
            onChange={(e) => setLease({ ...lease, ends_on: e.target.value })} />
          <Field label="Rent a month" id="lease-rent" inputMode="decimal" required value={lease.rent}
            onChange={(e) => setLease({ ...lease, rent: e.target.value })} />
          <Field label="Rent due on day (1 to 28)" id="lease-due-day" type="number" min={1} max={28} required
            value={lease.due} onChange={(e) => setLease({ ...lease, due: e.target.value })} />
          <fieldset className="flex flex-col gap-2 text-sm text-ink-muted">
            <legend className="mb-1">On the lease</legend>
            {tenants.length === 0 && <span>No tenant of {propertyName} yet.</span>}
            {tenants.map((t) => (
              <label key={t.id} className="flex items-center gap-2 text-ink">
                <input type="checkbox" checked={chosen.includes(t.id)} onChange={(e) => toggle(t.id, e.target.checked)} />
                {t.name}
              </label>
            ))}
          </fieldset>
          <div className="flex flex-wrap items-end gap-3">
            <Field label="Tenant's name" id="tenant-name" maxLength={200} value={tenant} onChange={(e) => setTenant(e.target.value)} />
            <Button type="button" variant="secondary" disabled={!tenant.trim()} onClick={async () => {
              const name = tenant.trim();
              const added = await adding.record<{ id: string }>("/api/setup/tenants", { property_id: propertyId, name }, (made) => {
                setChosen((now) => [...now, made.id]);
                return `Added ${name} as a tenant of ${propertyName}.`;
              });
              if (added) setTenant("");
            }}>Add tenant</Button>
          </div>
          <Said>{adding.said}</Said>
          <div><Button type="submit" disabled={chosen.length === 0}>Add lease</Button></div>
          <Notice tone="error">{adding.error || error}</Notice>
        </Form>
      )}
    </SidePanel>
  );
}
