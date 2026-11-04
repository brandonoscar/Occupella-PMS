// A lease's ledger as the API sends it (trust_report_tenant_ledger's rows), split for the page:
// the lease it is for, each charge and payment with the balance after it, and the totals.

import { sumMoney, usDatesIn } from "./money";
import type { StatementData } from "./statement";

export type LedgerSource = { kind: "charge" | "payment" | "returned"; id: string };

// A payment row in words (src/pms/api/ledger_sources.py): "Rent payment", how it came ("Check
// 2101", null when nobody recorded it) and its receipt.
export type PaymentLine = { what: string; how: string | null; receipt: string | null };

export type Entry = {
  on: string;
  item: string;
  detail: string;
  charged: string;
  paid: string;
  balance: string;
  source: LedgerSource | null;
  payment: PaymentLine | null;
};

// The tenant's ledger as the API sends it: each row's charge, payment or returned payment, which
// opens where it came from (src/pms/api/ledger_sources.py); null for the lease and the balance.
// What a payment held as prepaid rent: a line of its own, neither charged nor paid, so the
// balance stays; it counts as paid on its "Prepaid rent used" line.
export type Held = {
  on: string;
  what: string;
  how: string | null;
  amount: string;
  receipt: string;
  returned: boolean;
};

export type LedgerData = Omit<StatementData, "sources"> & {
  sources?: (LedgerSource | null)[];
  payments?: (PaymentLine | null)[];
  held?: Held[];
};

export type Ledger = {
  lease: string;
  // What was owed before the first line: the closing balance less what was charged and paid.
  opening: string;
  entries: Entry[];
  held: Held[];
  through: string;
  totals: { charged: string; paid: string; balance: string };
};

export function readLedger(data: LedgerData): Ledger {
  const at = (name: string) => {
    const index = data.columns.indexOf(name);
    if (index < 0) throw new Error(`the ledger has no ${name} column`);
    return index;
  };
  const [on, item, detail, charged, paid, balance] = [
    at("on_date"),
    at("item"),
    at("detail"),
    at("charged"),
    at("paid"),
    at("balance"),
  ];
  const ledger: Ledger = {
    lease: "",
    opening: "0.00",
    entries: [],
    held: data.held ?? [],
    through: "",
    totals: { charged: "", paid: "", balance: "" },
  };
  for (const [n, row] of data.rows.entries()) {
    const cell = (index: number) => row[index] ?? "";
    if (cell(item) === "lease") {
      ledger.lease = cell(detail);
    } else if (cell(item) === "balance") {
      ledger.through = cell(on);
      ledger.totals = { charged: cell(charged), paid: cell(paid), balance: cell(balance) };
    } else {
      ledger.entries.push({
        on: cell(on),
        item: cell(item),
        detail: cell(detail),
        charged: cell(charged),
        paid: cell(paid),
        balance: cell(balance),
        source: data.sources?.[n] ?? null,
        payment: data.payments?.[n] ?? null,
      });
    }
  }
  const { charged: c, paid: p, balance: b } = ledger.totals;
  if (b) ledger.opening = sumMoney([b, negated(c || "0"), p || "0"]);
  return ledger;
}

function negated(amount: string): string {
  return amount.startsWith("-") ? amount.slice(1) : `-${amount}`;
}

// What a ledger line is called on the page.
export function itemLabel(item: string): string {
  return { rent: "Rent", fee: "Fee", credit: "Credit", payment: "Payment" }[item] ??
    (item === "bounced payment" ? "Payment returned" : item);
}

// A ledger line in words: its kind, then its memo, unless the memo already says the kind
// ("Rent due 08/01/2026" is not "Rent: Rent due 08/01/2026"); a day in the memo reads MM/DD/YYYY.
// A payment reads as what paid and how ("Rent payment", "Check 2101"), never its memo.
export function entryWords(
  entry: Pick<Entry, "item" | "detail"> & { payment?: PaymentLine | null },
): { what: string; detail: string } {
  if (entry.payment) return { what: entry.payment.what, detail: entry.payment.how ?? "" };
  const what = itemLabel(entry.item);
  const detail = usDatesIn(entry.detail);
  if (detail.toLowerCase().startsWith(what.toLowerCase())) {
    return { what: detail, detail: "" };
  }
  return { what, detail };
}

// The ledger's lines in order: each payment's prepaid part after the lines of its day and those
// before it, with the balance as it stood then (the opening balance before any line).
export type Line = { entry: Entry } | { held: Held; balance: string };

export function ledgerLines(ledger: Ledger): Line[] {
  const lines: Line[] = [];
  const held = [...ledger.held];
  let balance = ledger.opening;
  for (const entry of ledger.entries) {
    while (held.length > 0 && (held[0] as Held).on < entry.on) {
      lines.push({ held: held.shift() as Held, balance });
    }
    lines.push({ entry });
    balance = entry.balance;
  }
  for (const rest of held) lines.push({ held: rest, balance });
  return lines;
}
