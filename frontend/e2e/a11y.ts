// Every page the browser tests reach is checked against WCAG 2.2 A and AA by axe-core, as far as
// a machine can check it (contrast, names, roles, structure, target sizes); what it can't judge,
// such as whether a name makes sense, stays with the people reading the page.
import AxeBuilder from "@axe-core/playwright";
import { expect, type Page } from "@playwright/test";

const WCAG = ["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22a", "wcag22aa"];

// Each violation as "rule (impact): first elements", so a failure says what to fix.
async function violations(page: Page): Promise<string[]> {
  const result = await new AxeBuilder({ page }).withTags(WCAG).analyze();
  return result.violations.map(
    (v) => `${v.id} (${v.impact}): ${v.nodes.slice(0, 3).map((n) => n.target.join(" ")).join(", ")}`,
  );
}

export async function expectAccessible(page: Page, where = page.url()): Promise<void> {
  expect(await violations(page), `axe on ${where}`).toEqual([]);
}
