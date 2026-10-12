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
  apply_token: string;
  leasing_email: string;
  leasing_invite: string;
  offers_invite: string;
  renewer_invite: string;
};
const seed: Seed = JSON.parse(readFileSync(new URL("./.seed.json", import.meta.url), "utf8"));
const PASSWORD = "a long synthetic passphrase";
const day = (fromNow: number) => new Date(Date.now() + fromNow * 86_400_000).toISOString().slice(0, 10);

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

test("an applicant applies; staff screen, decline with a notice, and the applicant sees it", async ({
  page,
  browser,
}) => {
  await page.goto(`/apply#${seed.apply_token}`);
  await expect(page.getByRole("heading", { name: "Apply for E2E Property 3, E2E Unit 2" })).toBeVisible();
  const moveIn = new Date(Date.now() + 30 * 86_400_000).toISOString().slice(0, 10);
  await page.getByLabel("Move-in day").fill(moveIn);
  await page.getByLabel("Monthly income of everyone applying, together").fill("1500");
  await page.getByLabel("Full name").fill("Applicant E2E");
  await page.getByLabel("Email").fill("applicant.e2e@example.com");
  await page.getByRole("button", { name: "Send application" }).click();
  const statusUrl = (await page.getByTestId("status-url").textContent()) ?? "";
  expect(statusUrl).toContain("/application#");

  // Staff, in their own browser.
  const staff = await (await browser.newContext()).newPage();
  await staff.goto(`/invite#${seed.leasing_invite}`);
  await staff.getByLabel("Choose a password (12 characters or more)").fill(PASSWORD);
  await staff.getByLabel("The same password again").fill(PASSWORD);
  await staff.getByRole("button", { name: "Set password" }).click();
  await staff.getByLabel("Code").fill(totp((await staff.locator("code").textContent()) ?? ""));
  await staff.getByRole("button", { name: "Finish" }).click();

  const application = staff.getByRole("listitem", { name: "Application from Applicant E2E" });
  await application.getByRole("button", { name: "Screen" }).click();
  await expect(application).toContainText("Fake Screening Partner: Recommends declining");
  await application.getByLabel("Decision").selectOption({ label: "Decline" });
  await expect(application.getByLabel("Reasons, one a line")).toHaveValue(
    "Household income is less than twice the rent",
  );
  await application.getByRole("button", { name: "Record decision" }).click();
  await expect(application).toHaveCount(0);
  const notice = await staff.request.get(
    (await staff.getByRole("link", { name: "Notice (PDF)" }).getAttribute("href")) ?? "",
  );
  expect(notice.headers()["content-type"]).toBe("application/pdf");

  const path = new URL(statusUrl).pathname + new URL(statusUrl).hash;
  await page.goto(path);
  await expect(page.getByTestId("application-status")).toContainText("Declined");
});

test("staff offer a lease and a renewal; the signers sign on the partner's page; both become leases", async ({
  page,
  browser,
}) => {
  await page.goto(`/invite#${seed.offers_invite}`);
  await page.getByLabel("Choose a password (12 characters or more)").fill(PASSWORD);
  await page.getByLabel("The same password again").fill(PASSWORD);
  await page.getByRole("button", { name: "Set password" }).click();
  await page.getByLabel("Code").fill(totp((await page.locator("code").textContent()) ?? ""));
  await page.getByRole("button", { name: "Finish" }).click();
  const document = {
    name: "lease.pdf",
    mimeType: "application/pdf",
    buffer: Buffer.from("%PDF-1.4 a synthetic lease for the browser test"),
  };

  // A lease for the approved application; the applicant signs from the link staff send them.
  const choose = page.getByRole("combobox", { name: "Offer", exact: true });
  await choose.selectOption({ label: "Mover E2E for E2E Property 3, E2E Unit 4" });
  await expect(page.getByLabel("Starts")).toHaveValue(day(30));
  await page.getByLabel("Lease document (PDF, your own)").setInputFiles(document);
  await page.getByRole("button", { name: "Send for signing" }).click();
  const offer = page.getByRole("listitem", { name: "Offer for E2E Unit 4" });
  const link = new URL((await offer.getByTestId("signing-link").textContent()) ?? "");
  const pdf = await page.request.get(
    (await offer.getByRole("link", { name: "Lease (PDF)" }).getAttribute("href")) ?? "",
  );
  expect(await pdf.body()).toEqual(document.buffer);

  const mover = await (await browser.newContext()).newPage();
  await mover.goto(link.pathname + link.hash);
  await expect(mover.getByText("For Mover E2E")).toBeVisible();
  await mover.getByLabel("Type your name as it is on the lease").fill("Someone Else");
  await mover.getByRole("button", { name: "Sign" }).click();
  await expect(mover.getByRole("alert")).toHaveText("type your name as it is on the lease");
  await mover.getByLabel("Type your name as it is on the lease").fill("mover e2e");
  await mover.getByRole("button", { name: "Sign" }).click();
  await expect(mover.getByTestId("esign-status")).toHaveText("Signed. You can close this page.");

  await offer.getByRole("button", { name: "Make it the lease" }).click();
  await expect(page.getByText(`E2E Property 3, E2E Unit 4 has its lease from ${day(30)}.`)).toBeVisible();
  await expect(offer).toHaveCount(0);

  // The renewal of a tenant's lease; the tenant signs it from their portal.
  await choose.selectOption({ label: "E2E Property 3, E2E Unit 3: Renewer E2E" });
  await page.getByLabel("Starts").fill(day(60));
  await page.getByLabel("Lease document (PDF, your own)").setInputFiles(document);
  await page.getByRole("button", { name: "Send for signing" }).click();
  const renewal = page.getByRole("listitem", { name: "Offer for E2E Unit 3" });
  await expect(renewal).toContainText("Signed by 0 of 1.");

  const tenant = await (await browser.newContext()).newPage();
  await tenant.goto(`/invite#${seed.renewer_invite}`);
  await tenant.getByLabel("Choose a password (12 characters or more)").fill(PASSWORD);
  await tenant.getByLabel("The same password again").fill(PASSWORD);
  await tenant.getByRole("button", { name: "Set password" }).click();
  const offered = tenant.getByRole("listitem", { name: "Renewal of E2E Unit 3" });
  await expect(offered).toContainText(`${day(60)} to month to month, $1,000.00 a month`);
  await offered.getByRole("button", { name: "Sign" }).click();
  await tenant.getByLabel("Type your name as it is on the lease").fill("Renewer E2E");
  await tenant.getByRole("button", { name: "Sign" }).click();
  await expect(tenant.getByTestId("esign-status")).toBeVisible();
  await tenant.goto("/");
  await expect(tenant.getByTestId("renewal-signed")).toContainText("You signed");

  await renewal.getByRole("button", { name: "Check signatures" }).click();
  await expect(renewal).toContainText("Signed by everyone");
  await renewal.getByRole("button", { name: "Make it the lease" }).click();
  await expect(renewal).toHaveCount(0);
  await tenant.reload();
  await expect(tenant.getByRole("heading", { name: "Welcome, Renewer E2E" })).toBeVisible();
  await expect(tenant.getByRole("heading", { name: "Lease renewal" })).toHaveCount(0);
});
