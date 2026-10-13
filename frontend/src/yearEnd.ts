// An owner's year-end package as the API sends it (trust_report_owner_year_end's rows), grouped
// for the page: each property's parts of the year, the same across all properties, and the
// rents for the owner's 1099-MISC.

export type YearEndData = {
  title: string;
  columns: string[];
  rows: (string | null)[][];
};

export type Part = { item: string; amount: string };
export type PropertyYear = { name: string; parts: Part[] };
export type YearEnd = { year: string; properties: PropertyYear[]; totals: Part[]; rents: string };

const TOTAL = "all properties: ";
const RENTS = "1099-MISC rents (box 1)";

export function groupYearEnd(data: YearEndData): YearEnd {
  const at = (name: string) => {
    const index = data.columns.indexOf(name);
    if (index < 0) throw new Error(`the year-end package has no ${name} column`);
    return index;
  };
  const [property, item, amount] = [at("property"), at("item"), at("amount")];
  const grouped: YearEnd = { year: "", properties: [], totals: [], rents: "0.00" };
  for (const row of data.rows) {
    const value = (index: number) => row[index] ?? "";
    const what = value(item);
    if (what === "year") grouped.year = value(property);
    else if (what === RENTS) grouped.rents = value(amount);
    else if (what.startsWith(TOTAL)) grouped.totals.push({ item: what.slice(TOTAL.length), amount: value(amount) });
    else if (what !== "PMC" && what !== "owner") {
      let current = grouped.properties.at(-1);
      if (current?.name !== value(property)) {
        current = { name: value(property), parts: [] };
        grouped.properties.push(current);
      }
      current.parts.push({ item: what, amount: value(amount) });
    }
  }
  return grouped;
}

// Last calendar year (UTC): the year an owner usually wants their package for.
export function lastYear(today: Date): number {
  return today.getUTCFullYear() - 1;
}

// A part's name as the page shows it.
export function partLabel(item: string): string {
  return item.charAt(0).toUpperCase() + item.slice(1);
}
