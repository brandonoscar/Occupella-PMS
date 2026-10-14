// The owner statement as the API sends it (trust_report_owner_statement's rows), grouped for
// the page: each property's postings between its opening and closing balance, then the totals
// over all properties.

export type StatementData = {
  title: string;
  columns: string[];
  rows: (string | null)[][];
};

export type Posting = { postedOn: string; detail: string; amount: string; balance: string };

export type PropertySection = {
  name: string;
  opening: string;
  postings: Posting[];
  closing: string;
};

export type Grouped = {
  period: string;
  properties: PropertySection[];
  totals: { opening: string; moneyIn: string; moneyOut: string; closing: string };
};

export function group(data: StatementData): Grouped {
  const at = (name: string) => {
    const index = data.columns.indexOf(name);
    if (index < 0) throw new Error(`the statement has no ${name} column`);
    return index;
  };
  const [property, postedOn, item, detail, amount, balance] = [
    at("property"),
    at("posted_on"),
    at("item"),
    at("detail"),
    at("amount"),
    at("balance"),
  ];
  const grouped: Grouped = {
    period: "",
    properties: [],
    totals: { opening: "0.00", moneyIn: "0.00", moneyOut: "0.00", closing: "0.00" },
  };
  let current: PropertySection | null = null;
  for (const row of data.rows) {
    const value = (index: number) => row[index] ?? "";
    switch (value(item)) {
      case "period":
        grouped.period = value(detail);
        break;
      case "opening balance":
        current = { name: value(property), opening: value(balance), postings: [], closing: "" };
        grouped.properties.push(current);
        break;
      case "posting":
        current?.postings.push({
          postedOn: value(postedOn),
          detail: value(detail),
          amount: value(amount),
          balance: value(balance),
        });
        break;
      case "closing balance":
        if (current) current.closing = value(balance);
        break;
      case "all properties: opening balance":
        grouped.totals.opening = value(balance);
        break;
      case "all properties: money in":
        grouped.totals.moneyIn = value(amount);
        break;
      case "all properties: money out":
        grouped.totals.moneyOut = value(amount);
        break;
      case "all properties: closing balance":
        grouped.totals.closing = value(balance);
        break;
    }
  }
  return grouped;
}

// A posting as an owner reads it. The report names the other side of each posting by its ledger
// kind, then its name, then the posting's memo ("bank_cash: Demo operating trust account -
// Owner draw"); the page says what it was ("Owner draw") and with whom (the bank, the vendor,
// the management company). Without a memo, the kind says what it was. The report's own text
// stays as it is in the downloads.
const WITHOUT_MEMO: Record<string, (amount: string) => string> = {
  bank_cash: (amount) => (amount.startsWith("-") ? "Paid out" : "Received"),
  pmc_income: () => "Management company",
  vendor_payable: () => "Vendor",
  owner_property: () => "Transfer",
  prepaid_rent: () => "Prepaid rent",
  tenant_deposit: () => "Security deposit",
};

export function describePosting(detail: string, amount: string): { what: string; with: string } {
  const match = /^([a-z_]+): (.*)$/s.exec(detail);
  const said = match && WITHOUT_MEMO[match[1] ?? ""];
  if (!match || !said) return { what: detail, with: "" };
  const rest = match[2] ?? "";
  const dash = rest.indexOf(" - ");
  if (dash < 0) return { what: said(amount), with: rest };
  return { what: rest.slice(dash + 3), with: rest.slice(0, dash) };
}

// The report's period ("2026-09-01 00:00:00 to 2026-10-01 00:00:00 UTC", its end not included)
// in words: "September 1 to September 30, 2026". Anything else is shown as it came.
const LONG = new Intl.DateTimeFormat("en-US", {
  month: "long",
  day: "numeric",
  year: "numeric",
  timeZone: "UTC",
});
const MONTH_DAY = new Intl.DateTimeFormat("en-US", { month: "long", day: "numeric", timeZone: "UTC" });

export function periodLabel(period: string): string {
  const match = /^(\d{4}-\d{2}-\d{2}) 00:00:00 to (\d{4}-\d{2}-\d{2}) 00:00:00 UTC$/.exec(period);
  if (!match) return period;
  const start = new Date(`${match[1]}T00:00:00Z`);
  const last = new Date(Date.parse(`${match[2]}T00:00:00Z`) - 86_400_000);
  if (last < start) return period;
  const sameYear = start.getUTCFullYear() === last.getUTCFullYear();
  return `${sameYear ? MONTH_DAY.format(start) : LONG.format(start)} to ${LONG.format(last)}`;
}
