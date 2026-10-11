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
