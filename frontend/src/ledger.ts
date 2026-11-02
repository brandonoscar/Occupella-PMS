// A lease's ledger as the API sends it (trust_report_tenant_ledger's rows), split for the page:
// the lease it is for, each charge and payment with the balance after it, and the totals.

import { usDatesIn } from "./money";
import type { StatementData } from "./statement";

export type Entry = {
  on: string;
  item: string;
  detail: string;
  charged: string;
  paid: string;
  balance: string;
};

export type Ledger = {
  lease: string;
  entries: Entry[];
  through: string;
  totals: { charged: string; paid: string; balance: string };
};

export function readLedger(data: StatementData): Ledger {
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
    entries: [],
    through: "",
    totals: { charged: "", paid: "", balance: "" },
  };
  for (const row of data.rows) {
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
      });
    }
  }
  return ledger;
}

// What a ledger line is called on the page.
export function itemLabel(item: string): string {
  return { rent: "Rent", fee: "Fee", credit: "Credit", payment: "Payment" }[item] ??
    (item === "bounced payment" ? "Payment returned" : item);
}

// A ledger line in words: its kind, then its memo, unless the memo already says the kind
// ("Rent due 08/01/2026" is not "Rent: Rent due 08/01/2026"); a day in the memo reads MM/DD/YYYY.
export function entryWords(entry: Pick<Entry, "item" | "detail">): { what: string; detail: string } {
  const what = itemLabel(entry.item);
  const detail = usDatesIn(entry.detail);
  if (detail.toLowerCase().startsWith(what.toLowerCase())) {
    return { what: detail, detail: "" };
  }
  return { what, detail };
}
