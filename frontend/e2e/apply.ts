import type { Page } from "@playwright/test";

// What the application form asks each applicant besides their name and email (ADR 0010 phase 2,
// part G): a phone, where they live now and an emergency contact. Synthetic: fictional phones
// (555-0100 to 555-0199) and a sample street.
export async function fillApplicant(page: Page, n: number, name: string, email: string): Promise<void> {
  const at = (label: string) => page.getByLabel(label, { exact: true }).nth(n);
  await at("Full name").fill(name);
  await at("Email").fill(email);
  await at("Phone").fill(`555-555-01${10 + n}`);
  await at("Street address").fill(`${n + 1} Sample Street`);
  await at("City").fill("Sampleton");
  await at("State").fill("ST");
  await at("ZIP code").fill("00001");
  await at("Emergency contact's name").fill(`Contact of ${name}`);
  await at("Emergency contact's phone").fill("555-555-0160");
}
