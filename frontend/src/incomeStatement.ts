// An owner's income statement as the API sends it (trust_report_income_statement's rows),
// grouped for the page: each property's income, expenses, net income and Schedule E lines, and
// the same across all properties.
import type { YearEndData } from "./yearEnd";

export type Line = { label: string; amount: string };
export type PropertyStatement = {
  name: string;
  income: Line[];
  totalIncome: string;
  expenses: Line[];
  totalExpenses: string;
  net: string;
  scheduleE: Line[];
};
export type IncomeStatement = { period: string; sections: PropertyStatement[] };

const TOTAL = "all properties: ";

function empty(name: string): PropertyStatement {
  return { name, income: [], totalIncome: "0.00", expenses: [], totalExpenses: "0.00", net: "0.00", scheduleE: [] };
}

// "income: late fees" reads "Late fees"; "expense: Repairs" reads "Repairs".
function label(item: string, prefix: string): string {
  const rest = item.slice(prefix.length);
  return rest.charAt(0).toUpperCase() + rest.slice(1);
}

export function groupIncomeStatement(data: YearEndData): IncomeStatement {
  const at = (name: string) => {
    const index = data.columns.indexOf(name);
    if (index < 0) throw new Error(`the income statement has no ${name} column`);
    return index;
  };
  const [property, item, amount] = [at("property"), at("item"), at("amount")];
  const grouped: IncomeStatement = { period: "", sections: [] };
  const sections = new Map<string, PropertyStatement>();
  for (const row of data.rows) {
    const value = (index: number) => row[index] ?? "";
    let what = value(item);
    if (what === "period") {
      grouped.period = value(property);
      continue;
    }
    if (what === "PMC" || what === "owner") continue;
    let name = value(property);
    if (what.startsWith(TOTAL)) {
      what = what.slice(TOTAL.length);
      name = "All properties";
    }
    let section = sections.get(name);
    if (!section) {
      section = empty(name);
      sections.set(name, section);
      grouped.sections.push(section);
    }
    const money = value(amount);
    if (what.startsWith("income: ")) section.income.push({ label: label(what, "income: "), amount: money });
    else if (what.startsWith("expense: ")) section.expenses.push({ label: label(what, "expense: "), amount: money });
    else if (what === "total income") section.totalIncome = money;
    else if (what === "total expenses") section.totalExpenses = money;
    else if (what === "net income") section.net = money;
    else if (what.startsWith("Schedule E")) section.scheduleE.push({ label: what, amount: money });
  }
  return grouped;
}
