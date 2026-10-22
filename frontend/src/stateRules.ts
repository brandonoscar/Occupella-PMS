import { useEffect, useState } from "react";
import { api } from "./api";
import { latest } from "./latest";

// State rules as warnings (q142, src/pms/state_rules.py): a check of terms staff set against the
// numbers some states' statutes set (late fees, notice of a rent increase, notice before entering
// a home), and the table on Settings, State rules. A check only warns; the page saves either way.
// No attorney has reviewed the table, and every warning says so.

export type How = "official" | "excerpt" | "secondary";

export type StateWarning = { rule: string; said: string; cite: string; how: How };

export type Checked = {
  // The property's state, from its address; null with no address on file.
  state: string | null;
  // Whether the table has a rule of this kind for the state.
  rules: boolean;
  warnings: StateWarning[];
  note: string;
};

export type Rule = { words: string; cite: string; url: string; how: How };

export type StateRow = {
  state: string;
  name: string;
  late_fee: Rule | null;
  rent_increase: Rule | null;
  entry: Rule | null;
};

export type Table = { checked_on: string; note: string; states: StateRow[] };

// How a rule was checked, in words.
export const HOW: Record<How, string> = {
  official: "read in the statute",
  excerpt: "the statute's words, quoted in a search excerpt",
  secondary: "a summary only",
};

// A check's query from what a form holds, leaving out what is empty.
export function checkQuery(params: Record<string, string | null | undefined>): string {
  const query = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value !== null && value !== undefined && value.trim() !== "") query.set(key, value.trim());
  }
  return query.toString();
}

// An answer that is a check's, and not something else the server said.
function isChecked(answer: unknown): answer is Checked {
  return typeof answer === "object" && answer !== null && Array.isArray((answer as Checked).warnings);
}

// The answer to a check, asked again a moment after what it checks stops changing (a form being
// typed in) or `again` does (the page's record, refreshed after a save); null while there is
// nothing to check or the check was refused (a number half typed).
export function useStateCheck(path: string | null, again?: unknown): Checked | null {
  const [found, setFound] = useState<Checked | null>(null);
  useEffect(() => {
    const run = latest();
    if (path === null) {
      setFound(null);
      return run.stop;
    }
    const timer = window.setTimeout(() => {
      api.get<unknown>(path)
        .then(run.keep((answer: unknown) => setFound(isChecked(answer) ? answer : null)))
        .catch(run.keep(() => setFound(null)));
    }, 250);
    return () => {
      window.clearTimeout(timer);
      run.stop();
    };
  }, [path, again]);
  return found;
}
