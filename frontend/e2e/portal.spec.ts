// The portal in a real browser, against the API and a database seeded by e2e/seed.py.
import { readFileSync } from "node:fs";
import { expect, test } from "@playwright/test";
import { totp } from "./totp";

type Seed = {
  pmc_id: string;
  owner_email: string;
  owner_invite: string;
  staff_email: string;
  staff_invite: string;
  renter_email: string;
  renter_invite: string;
};
const seed: Seed = JSON.parse(readFileSync(new URL("./.seed.json", import.meta.url), "utf8"));
const PASSWORD = "a long synthetic passphrase";

// The pages are served under a strict Content-Security-Policy (src/pms/api/app.py). Anything it
// blocks, such as a script or an image from another origin, fails the test that loaded it.
test.beforeEach(async ({ page }) => {
  await page.addInitScript(() => {
    const seen: string[] = [];
    (window as unknown as { cspViolations: string[] }).cspViolations = seen;
    document.addEventListener("securitypolicyviolation", (e) =>
      seen.push(`${e.violatedDirective} ${e.blockedURI}`),
    );
  });
});

test.afterEach(async ({ page }) => {
  const seen = await page.evaluate(
    () => (window as unknown as { cspViolations: string[] }).cspViolations,
  );
  expect(seen).toEqual([]);
});

test("an owner joins from the invite, reads their statement and signs back in", async ({ page }) => {
  await page.goto(`/invite#${seed.owner_invite}`);
  await expect(page.getByRole("heading", { name: "Welcome, Owner E2E" })).toBeVisible();
  await page.getByLabel("Choose a password (12 characters or more)").fill(PASSWORD);
  await page.getByLabel("The same password again").fill(PASSWORD);
  await page.getByRole("button", { name: "Set password" }).click();

  await expect(page.getByRole("heading", { name: "Your properties" })).toBeVisible();
  const holdings = page.locator("section", { hasText: "What each property holds today" });
  await expect(holdings.getByRole("row", { name: /E2E Property 1/ })).toContainText("$1,500.00");
  await expect(holdings.getByRole("row", { name: /E2E Property 2/ })).toContainText("$1,250.00");

  const statement = page.locator("section", { hasText: "Statement" });
  await expect(statement.getByText("Closing, all properties")).toBeVisible();
  await expect(statement.locator("dd").last()).toHaveText("$2,750.00");
  const pdf = await page.request.get(
    (await statement.getByRole("link", { name: "PDF" }).getAttribute("href")) ?? "",
  );
  expect(pdf.status()).toBe(200);
  expect(pdf.headers()["content-type"]).toBe("application/pdf");

  await page.getByRole("button", { name: "Sign out" }).click();
  await expect(page.getByRole("heading", { name: "Sign in" })).toBeVisible();
  await page.getByLabel("Email").fill(seed.owner_email);
  await page.getByLabel("Password").fill(PASSWORD);
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page.getByRole("heading", { name: "Your properties" })).toBeVisible();
});

test("a wrong password is refused without saying which part was wrong", async ({ page }) => {
  await page.goto(`/sign-in?pmc=${seed.pmc_id}`);
  await page.getByLabel("Email").fill(seed.owner_email);
  await page.getByLabel("Password").fill("not the password at all");
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page.getByRole("alert")).toHaveText("the email or password is wrong");
});

test("a tenant joins, sees what is due and asks for a repair", async ({ page }) => {
  await page.goto(`/invite#${seed.renter_invite}`);
  await page.getByLabel("Choose a password (12 characters or more)").fill(PASSWORD);
  await page.getByLabel("The same password again").fill(PASSWORD);
  await page.getByRole("button", { name: "Set password" }).click();

  await expect(page.getByRole("heading", { name: "Welcome, Tenant Two E2E" })).toBeVisible();
  await expect(page.getByTestId("balance-due")).toHaveText("$950.00");
  const ledger = page.locator("section", { hasText: "Charges and payments" });
  await expect(ledger.getByRole("row", { name: /Check 101/ })).toContainText("$950.00");
  await expect(ledger.getByRole("row", { name: /Balance at the end of/ })).toContainText("$950.00");

  await page.getByLabel("What needs fixing?").fill("The hallway light is out");
  await page.getByLabel("Someone may come in while I'm out").check();
  await page.getByRole("button", { name: "Send request" }).click();
  const requests = page.locator("section", { hasText: "Maintenance requests" });
  await expect(requests.getByRole("listitem").filter({ hasText: "The hallway light is out" }))
    .toContainText("Received");
  await expect(requests.getByText("The back door sticks")).toBeVisible();
});

test("staff enroll an authenticator, invite a tenant and see a repair through", async ({ page }) => {
  await page.goto(`/invite#${seed.staff_invite}`);
  await page.getByLabel("Choose a password (12 characters or more)").fill(PASSWORD);
  await page.getByLabel("The same password again").fill(PASSWORD);
  await page.getByRole("button", { name: "Set password" }).click();

  const secret = (await page.locator("code").textContent()) ?? "";
  expect(secret).toMatch(/^[A-Z2-7]{16,}$/);
  await page.getByLabel("Code").fill(totp(secret));
  await page.getByRole("button", { name: "Finish" }).click();

  const people = page.locator("section", { hasText: "People who can sign in" });
  const tenant = people.getByRole("row", { name: /Tenant E2E/ });
  await expect(tenant).toContainText("no login");
  await tenant.getByLabel("Email for Tenant E2E").fill("tenant.e2e@example.com");
  await tenant.getByRole("button", { name: "Invite" }).click();
  await expect(tenant).toContainText("/invite#");
  await expect(tenant).toContainText("invited");

  // The seeded request: a work order, a vendor, then done.
  const request = page.getByRole("listitem", { name: "The back door sticks" });
  await request.getByRole("button", { name: "Open work order" }).click();
  await expect(request).toContainText("Work order opened");
  await request.getByLabel("Vendor for The back door sticks").selectOption({ label: "Vendor E2E" });
  await request.getByRole("button", { name: "Assign" }).click();
  await expect(request).toContainText("Someone is assigned (Vendor E2E)");
  await request.getByRole("button", { name: "Done" }).click();
  await expect(request).toHaveCount(0);
});
