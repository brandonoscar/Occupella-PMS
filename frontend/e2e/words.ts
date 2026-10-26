// ADR 0010, rule 2: no internal words on a page people read. The ledger's kinds of account, the
// database's names, a value printed as nothing, the step a bill no longer has ("set aside"), the
// abbreviations staff don't use, and a day as the API writes it all stay off the page; inputs keep
// their own values (a date field holds YYYY-MM-DD), which a page's text doesn't include.
import { expect, type Page } from "@playwright/test";

const INTERNAL: [string, RegExp][] = [
  ["a name from the code", /(?<![\w.@-])[a-z]+_[a-z_]+(?![\w@-])/],
  ["set aside", /\bset aside\b/i],
  ["PMC", /\bPMCs?\b/],
  ["EFT", /\bEFT\b/],
  ["a value printed as nothing", /\b(undefined|null|NaN)\b|\[object Object\]|Invalid Date/],
  ["a day as YYYY-MM-DD", /(?<![\w-])\d{4}-\d{2}-\d{2}(?![\w-])/],
];

// Each internal word on the page's main part (the whole page where it has none, as sign-in
// doesn't), as "what: the line it is on".
export async function internalWords(page: Page): Promise<string[]> {
  const main = page.getByRole("main");
  const text = await ((await main.count()) > 0 ? main.first() : page.locator("body")).innerText();
  return text.split("\n").flatMap((line) =>
    INTERNAL.filter(([, pattern]) => pattern.test(line)).map(([what]) => `${what}: ${line.trim().slice(0, 120)}`),
  );
}

export async function expectPlainWords(page: Page, where = page.url()): Promise<void> {
  expect(await internalWords(page), `internal words on ${where}`).toEqual([]);
}
