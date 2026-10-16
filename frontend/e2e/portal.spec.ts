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
  setup_invite: string;
  rent_invite: string;
  bills_invite: string;
  month_end_invite: string;
  approver_invite: string;
  move_out_invite: string;
  documents_invite: string;
  switch_invite: string;
  demo_manager_invite: string;
  demo_owner_invite: string;
};
const seed: Seed = JSON.parse(readFileSync(new URL("./.seed.json", import.meta.url), "utf8"));
const PASSWORD = "a long synthetic passphrase";
const day = (fromNow: number) => new Date(Date.now() + fromNow * 86_400_000).toISOString().slice(0, 10);
// A day as the portal shows it: MM/DD/YYYY.
const us = (iso: string) => `${iso.slice(5, 7)}/${iso.slice(8, 10)}/${iso.slice(0, 4)}`;

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

  // By its heading: the year-end card below also says "statement" (case-insensitive hasText).
  const statement = page.locator("section", {
    has: page.getByRole("heading", { name: "Statement", exact: true }),
  });
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

  await page.getByRole("navigation", { name: "Sections" }).getByRole("link", { name: "Settings", exact: true }).click();
  await page.getByRole("list", { name: "Settings" }).getByRole("link", { name: "Logins" }).click();
  const tenant = page.getByRole("row", { name: /Tenant E2E/ });
  await expect(tenant).toContainText("No login");
  await tenant.getByLabel("Email for Tenant E2E").fill("tenant.e2e@example.com");
  await tenant.getByRole("button", { name: "Invite" }).click();
  await expect(tenant).toContainText("/invite#");
  await expect(tenant).toContainText("Invited");

  // The seeded request: a work order, a vendor, then done.
  await page.getByRole("navigation", { name: "Sections" }).getByRole("link", { name: "Work orders", exact: true }).click();
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
  await staff.getByRole("navigation", { name: "Sections" }).getByRole("link", { name: "Applicants", exact: true }).click();

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
  await page.getByRole("navigation", { name: "Sections" }).getByRole("link", { name: "Draft leases", exact: true }).click();
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
  await expect(page.getByText(`E2E Property 3, E2E Unit 4 has its lease from ${us(day(30))}.`)).toBeVisible();
  await expect(offer).toHaveCount(0);

  // The renewal of a tenant's lease; the tenant signs it from their portal.
  await choose.selectOption({ label: "E2E Property 3, E2E Unit 3: Renewer E2E" });
  await page.getByLabel("Starts").fill(day(60));
  await page.getByLabel("Lease document (PDF, your own)").setInputFiles(document);
  await page.getByRole("button", { name: "Send for signing" }).click();
  const renewal = page.getByRole("listitem", { name: "Offer for E2E Unit 3" });
  await expect(renewal).toContainText("Signed by 0 of 1.");
  // Staff can show each signer's link again.
  await renewal.getByRole("button", { name: "Signing links" }).click();
  await expect(renewal.getByTestId("signing-link")).toContainText("/esign#");

  const tenant = await (await browser.newContext()).newPage();
  await tenant.goto(`/invite#${seed.renewer_invite}`);
  await tenant.getByLabel("Choose a password (12 characters or more)").fill(PASSWORD);
  await tenant.getByLabel("The same password again").fill(PASSWORD);
  await tenant.getByRole("button", { name: "Set password" }).click();
  const offered = tenant.getByRole("listitem", { name: "Renewal of E2E Unit 3" });
  await expect(offered).toContainText(`${us(day(60))} – Month to month, $1,000.00 a month`);
  const read = await tenant.request.get(
    (await offered.getByRole("link", { name: "Read the lease (PDF)" }).getAttribute("href")) ?? "",
  );
  expect(await read.body()).toEqual(document.buffer);
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

test("staff set up a property from nothing, lease a unit and see it on the rent roll", async ({ page }) => {
  await page.goto(`/invite#${seed.setup_invite}`);
  await page.getByLabel("Choose a password (12 characters or more)").fill(PASSWORD);
  await page.getByLabel("The same password again").fill(PASSWORD);
  await page.getByRole("button", { name: "Set password" }).click();
  await page.getByLabel("Code").fill(totp((await page.locator("code").textContent()) ?? ""));
  await page.getByRole("button", { name: "Finish" }).click();
  await page.getByRole("navigation", { name: "Sections" }).getByRole("link", { name: "Settings", exact: true }).click();
  await page.getByRole("list", { name: "Settings" }).getByRole("link", { name: "Company setup" }).click();
  await expect(page.getByRole("heading", { name: "Company setup", level: 1 })).toBeVisible();

  const owners = page.getByRole("region", { name: "Owners" });
  await owners.getByLabel("Owner's name").fill("Owner Setup E2E");
  await owners.getByRole("button", { name: "Add" }).click();
  await expect(owners.getByText("Owner Setup E2E")).toBeVisible();

  await page.getByLabel("Property name").fill("E2E Property 9");
  await page.locator("#property-owner").selectOption({ label: "Owner Setup E2E" });
  await page.locator("#property-bank").selectOption({ label: "E2E operating trust account" });
  await page.getByRole("button", { name: "Add property" }).click();
  const property = page.getByRole("listitem", { name: "E2E Property 9" });
  await expect(property).toContainText("Owned by Owner Setup E2E.");
  await property.getByText("Add to E2E Property 9").click();
  await property.getByLabel("Unit name").fill("Unit 9A");
  await property.getByRole("button", { name: "Add unit" }).click();
  await expect(property).toContainText("Unit 9A: vacant");

  await page.getByLabel("Tenant of").selectOption({ label: "E2E Property 9" });
  await page.getByLabel("Tenant's name").fill("Tenant Setup E2E");
  await page.getByRole("button", { name: "Add tenant" }).click();
  await page
    .getByRole("combobox", { name: "Unit", exact: true })
    .selectOption({ label: "E2E Property 9, Unit 9A" });
  const today = day(0);
  await page.getByLabel("Starts").fill(today);
  await page.getByLabel("Rent a month").fill("1100.00");
  await page.getByLabel("Tenant Setup E2E").check();
  await page.getByRole("button", { name: "Add lease" }).click();
  await expect(property).toContainText(
    `Unit 9A: Tenant Setup E2E: ${us(today)} – Month to month, $1,100.00 a month`,
  );

  const rentRoll = await page.request.get(
    `/api/reports/trust_report_rent_roll?arg=${today}&format=csv`,
  );
  const csv = await rentRoll.text();
  expect(csv).toContain("E2E Property 9");
  expect(csv).toContain("Tenant Setup E2E");
});

test("staff record a tenant's payment, the rest held as prepaid rent, and then its bounce", async ({
  page,
}) => {
  await page.goto(`/invite#${seed.rent_invite}`);
  await page.getByLabel("Choose a password (12 characters or more)").fill(PASSWORD);
  await page.getByLabel("The same password again").fill(PASSWORD);
  await page.getByRole("button", { name: "Set password" }).click();
  await page.getByLabel("Code").fill(totp((await page.locator("code").textContent()) ?? ""));
  await page.getByRole("button", { name: "Finish" }).click();
  await page.getByRole("navigation", { name: "Sections" }).getByRole("link", { name: "Rent roll", exact: true }).click();

  // The rent roll says what the lease owes; its own page records its rent work.
  const row = page.getByRole("row", { name: "E2E Property 3, E2E Unit 5", exact: true });
  await expect(row.getByTestId("owed")).toHaveText("Owes $800.00");
  await row.getByRole("link", { name: "E2E Unit 5" }).click();
  await expect(page.getByRole("heading", { level: 1, name: "E2E Unit 5, E2E Property 3" })).toBeVisible();
  const lease = page.getByRole("listitem", { name: "E2E Property 3, E2E Unit 5" });
  await expect(lease.getByTestId("owed")).toHaveText("Owes $800.00");
  await lease.getByText("Record for E2E Unit 5").click();
  await lease.getByLabel("Payment received").fill("1000.00");
  await lease.getByLabel("Check or reference").fill("Money order 9");
  await lease.getByRole("button", { name: "Record payment" }).click();
  await expect(lease).toContainText("$800.00 paid what is owed; $200.00 held as prepaid rent.");
  await expect(lease.getByTestId("owed")).toHaveText("Paid up");
  await expect(lease).toContainText("Payer E2E: prepaid $200.00");
  await expect(lease.getByRole("row", { name: /Money order 9/ })).toBeVisible();

  // A bounce is asked for, with the day it came back (today by default), before it is recorded.
  await lease.getByRole("button", { name: /^Actions for / }).click();
  await lease.getByRole("menuitem", { name: "It bounced" }).click();
  await lease.getByRole("button", { name: "Record the $1,000.00 bounce" }).click();
  // The whole $1,000.00 comes back: the $800.00 it paid is owed again, and its $200.00 of
  // prepaid rent is gone.
  await expect(lease).toContainText("The $1,000.00 payment bounced and is undone.");
  await expect(lease.getByTestId("owed")).toHaveText("Owes $800.00");
  await expect(lease).toContainText("Payer E2E: prepaid $0.00");
  await expect(lease.getByText("Bounced", { exact: true })).toBeVisible();
});

test("staff enter a vendor's bill, record the owner's approval, set it aside and pay it, then draw the rest", async ({
  page,
}) => {
  await page.goto(`/invite#${seed.bills_invite}`);
  await page.getByLabel("Choose a password (12 characters or more)").fill(PASSWORD);
  await page.getByLabel("The same password again").fill(PASSWORD);
  await page.getByRole("button", { name: "Set password" }).click();
  await page.getByLabel("Code").fill(totp((await page.locator("code").textContent()) ?? ""));
  await page.getByRole("button", { name: "Finish" }).click();
  const sections = page.getByRole("navigation", { name: "Sections" });
  await sections.getByRole("link", { name: "Bills" }).click();

  await page.getByRole("button", { name: "Record bill" }).click();
  await page.getByRole("combobox", { name: "Vendor", exact: true }).selectOption({ label: "Vendor E2E" });
  await page
    .getByRole("combobox", { name: "Owner and property", exact: true })
    .selectOption({ label: "Payee E2E, E2E Property 7" });
  await page.getByLabel("Vendor's reference").fill("E2E-INV-1");
  await page.getByLabel("Amount", { exact: true }).fill("300.00");
  await page.getByLabel("What for").fill("Gutter repair");
  await page.getByRole("button", { name: "Enter bill" }).click();
  await expect(page.getByText("Bill E2E-INV-1 entered.")).toBeVisible();
  await page.getByRole("dialog", { name: "Record bill" }).getByRole("button", { name: "Close" }).click();

  // No agreement: every bill needs the owner's approval first.
  const bill = page.getByRole("listitem", { name: "Vendor E2E E2E-INV-1" });
  await expect(bill.getByTestId("status")).toHaveText("needs approval");
  await bill.getByLabel("Approved by").fill("Payee E2E");
  await bill.getByRole("button", { name: "Record approval" }).click();
  await expect(bill.getByTestId("status")).toHaveText("to set aside");
  await bill.getByRole("button", { name: "Set aside" }).click();
  await expect(bill.getByTestId("status")).toHaveText("set aside");
  await bill.getByRole("button", { name: "Pay" }).click();
  await bill.getByRole("button", { name: "Confirm" }).click();
  await expect(bill).toHaveCount(0);
  await page.getByRole("tab", { name: "All", exact: true }).click();
  await expect(bill.getByTestId("status")).toHaveText("paid");

  await sections.getByRole("link", { name: "Owners" }).click();
  await page.getByRole("row", { name: "Payee E2E", exact: true }).getByRole("link").click();
  const payee = page.getByRole("listitem", { name: "Payee E2E, E2E Property 7" });
  await expect(payee.getByTestId("available")).toHaveText("$700.00 available");
  await payee.getByText("Record for Payee E2E, E2E Property 7").click();
  await payee.getByRole("button", { name: "Record draw" }).click();
  await payee.getByRole("button", { name: "Confirm" }).click();
  await expect(payee).toContainText("$700.00 paid out to Payee E2E.");
  await expect(payee.getByTestId("available")).toHaveText("$0.00 available");
});

test("staff charge a tenant's damage at move-out, return the rest of the deposit and get the statement", async ({
  page,
}) => {
  await page.goto(`/invite#${seed.move_out_invite}`);
  await page.getByLabel("Choose a password (12 characters or more)").fill(PASSWORD);
  await page.getByLabel("The same password again").fill(PASSWORD);
  await page.getByRole("button", { name: "Set password" }).click();
  await page.getByLabel("Code").fill(totp((await page.locator("code").textContent()) ?? ""));
  await page.getByRole("button", { name: "Finish" }).click();
  await page.getByRole("navigation", { name: "Sections" }).getByRole("link", { name: "Move-outs", exact: true }).click();

  const card = page.getByRole("region", { name: "E2E Property 8 / E2E Unit 8: Leaver E2E" });
  await card.getByLabel("Cleaning or damage").fill("Carpet cleaning");
  await card.getByLabel("Amount").fill("150.00");
  await card.getByRole("button", { name: "Charge it" }).click();
  await expect(card.getByText("Charged $150.00: Carpet cleaning.")).toBeVisible();
  await expect(card.getByText(/Keeps \$150.00 for what the lease owes; \$750.00 goes back/)).toBeVisible();

  await card.getByRole("button", { name: "Return the deposit" }).click();
  await card.getByRole("button", { name: "Confirm" }).click();
  await expect(card.getByText("Returned $750.00 to the tenants; the rest paid what the lease owed.")).toBeVisible();
  await expect(card.getByLabel("Returned, E2E Property 8 / E2E Unit 8: Leaver E2E")).toContainText(
    "kept $150.00 of $900.00, $750.00 back to the tenants",
  );
  const statement = await page.request.get(
    (await card.getByRole("link", { name: "Statement for the tenants (PDF)" }).getAttribute("href")) ?? "",
  );
  expect(statement.status()).toBe(200);
  expect(statement.headers()["content-type"]).toBe("application/pdf");
});

test("staff keep an owner's document and the owner downloads it from the portal", async ({ page, browser }) => {
  await page.goto(`/invite#${seed.documents_invite}`);
  await page.getByLabel("Choose a password (12 characters or more)").fill(PASSWORD);
  await page.getByLabel("The same password again").fill(PASSWORD);
  await page.getByRole("button", { name: "Set password" }).click();
  await page.getByLabel("Code").fill(totp((await page.locator("code").textContent()) ?? ""));
  await page.getByRole("button", { name: "Finish" }).click();
  await page.getByRole("navigation", { name: "Sections" }).getByRole("link", { name: "Files", exact: true }).click();

  const pdf = Buffer.from("%PDF-1.4\n% synthetic management agreement\n");
  await page.getByRole("combobox", { name: "Kind" }).selectOption({ label: "Owner document" });
  await page.getByRole("combobox", { name: "Owner" }).selectOption({ label: "Owner E2E" });
  await page.getByLabel("File").setInputFiles({ name: "agreement-2026.pdf", mimeType: "application/pdf", buffer: pdf });
  await expect(page.getByLabel("Title")).toHaveValue("agreement-2026");
  await page.getByLabel("Title").fill("Management agreement 2026");
  await page.getByRole("button", { name: "Upload" }).click();
  await expect(page.getByText("Uploaded Management agreement 2026.")).toBeVisible();
  const kept = page.getByRole("list", { name: "Documents" }).getByRole("listitem");
  await expect(kept).toContainText("Owner document, Owner E2E");

  // The owner, who joined in the first test, signs in on their own and downloads it.
  const owner = await (await browser.newContext()).newPage();
  await owner.goto(`/sign-in?pmc=${seed.pmc_id}`);
  await owner.getByLabel("Email").fill(seed.owner_email);
  await owner.getByLabel("Password").fill(PASSWORD);
  await owner.getByRole("button", { name: "Sign in" }).click();
  const link = owner.getByRole("list", { name: "Your documents" }).getByRole("link", { name: "Management agreement 2026" });
  const file = await owner.request.get((await link.getAttribute("href")) ?? "");
  expect(file.status()).toBe(200);
  expect(file.headers()["content-type"]).toBe("application/pdf");
  expect(file.headers()["content-disposition"]).toBe('attachment; filename="Management-agreement-2026.pdf"');
  expect(Buffer.from(await file.body()).equals(pdf)).toBe(true);
  await owner.context().close();
});

// Last, since approving closes the operating trust account through last month.
test("staff import last month's statement, match it to the books and prepare it; another approves", async ({
  page,
}) => {
  const now = new Date();
  const first = new Date(Date.UTC(now.getUTCFullYear(), now.getUTCMonth() - 1, 1));
  const last = new Date(Date.UTC(now.getUTCFullYear(), now.getUTCMonth(), 0));
  const iso = (d: Date) => d.toISOString().slice(0, 10);
  const fifth = iso(new Date(first.getTime() + 4 * 86_400_000));
  const sixth = iso(new Date(first.getTime() + 5 * 86_400_000));

  await page.goto(`/invite#${seed.month_end_invite}`);
  await page.getByLabel("Choose a password (12 characters or more)").fill(PASSWORD);
  await page.getByLabel("The same password again").fill(PASSWORD);
  await page.getByRole("button", { name: "Set password" }).click();
  await page.getByLabel("Code").fill(totp((await page.locator("code").textContent()) ?? ""));
  await page.getByRole("button", { name: "Finish" }).click();
  await page.getByRole("navigation", { name: "Sections" }).getByRole("link", { name: "Banking", exact: true }).click();

  await page.getByRole("row", { name: "E2E operating trust account", exact: true }).getByRole("link").click();
  await expect(page.getByRole("heading", { level: 1, name: "E2E operating trust account" })).toBeVisible();
  // The seed's rents of last month: two owners' in one deposit, two more on their own.
  const csv = [
    "Date,Description,Amount,Reference",
    `${sixth},Deposit,"2,750.00",D-1`,
    `${sixth},Deposit,950.00,D-2`,
    `${sixth},Deposit,"1,000.00",D-3`,
  ].join("\n");
  await page
    .getByLabel("Statement file (CSV or OFX)")
    .setInputFiles({ name: "statement.csv", mimeType: "text/csv", buffer: Buffer.from(csv) });
  await expect(page.getByText("Ready: statement.csv.")).toBeVisible();
  await page.getByLabel("First day").fill(iso(first));
  await page.getByLabel("Last day").fill(iso(last));
  await page.getByRole("button", { name: "Import statement" }).click();
  await expect(page.getByText(`Imported 3 lines, ${us(iso(first))} to ${us(iso(last))}.`)).toBeVisible();

  await page.getByRole("button", { name: "Match the sure ones" }).click();
  await expect(page.getByText("2 lines matched.")).toBeVisible();
  const prepare = page.getByRole("button", { name: `Prepare through ${us(iso(last))}` });
  await expect(prepare).toBeDisabled();

  await page.getByLabel(`${us(sixth)} Deposit (D-1): $2,750.00`).check();
  await page.getByLabel(`${us(fifth)} Rent: $1,500.00`).check();
  await page.getByLabel(`${us(fifth)} Rent: $1,250.00`).check();
  await page.getByRole("button", { name: "Match these" }).click();
  await expect(page.getByText("Matched.")).toBeVisible();

  // A match made by mistake is undone, its line and transfers open again, and matched anew.
  const matches = page.getByRole("table", { name: "Matches" });
  await matches.getByRole("row", { name: /\(D-1\)/ }).getByRole("button", { name: "Undo" }).click();
  await expect(page.getByText("Match undone.")).toBeVisible();
  await expect(matches.getByRole("row", { name: /\(D-1\)/ })).toHaveCount(0);
  await expect(prepare).toBeDisabled();
  await page.getByLabel(`${us(sixth)} Deposit (D-1): $2,750.00`).check();
  await page.getByLabel(`${us(fifth)} Rent: $1,500.00`).check();
  await page.getByLabel(`${us(fifth)} Rent: $1,250.00`).check();
  await page.getByRole("button", { name: "Match these" }).click();
  await expect(page.getByText("Matched.")).toBeVisible();

  await prepare.click();
  await expect(page.getByLabel("Prepared")).toContainText(
    `Prepared through ${us(iso(last))} by Month-end E2E: bank $4,700.00, books $4,700.00.`,
  );
  await expect(page.getByLabel("Prepared")).toContainText("Another staff member who may approve approves it.");
  await page.getByRole("button", { name: "Sign out" }).click();

  // The approver, who may approve, signs in and approves what was prepared.
  await page.goto(`/invite#${seed.approver_invite}`);
  await page.getByLabel("Choose a password (12 characters or more)").fill(PASSWORD);
  await page.getByLabel("The same password again").fill(PASSWORD);
  await page.getByRole("button", { name: "Set password" }).click();
  await page.getByLabel("Code").fill(totp((await page.locator("code").textContent()) ?? ""));
  await page.getByRole("button", { name: "Finish" }).click();
  await page.getByRole("navigation", { name: "Sections" }).getByRole("link", { name: "Banking", exact: true }).click();
  await page.getByRole("row", { name: "E2E operating trust account", exact: true }).getByRole("link").click();
  await expect(page.getByRole("heading", { level: 1, name: "E2E operating trust account" })).toBeVisible();
  await page.getByRole("button", { name: `Approve through ${us(iso(last))}` }).click();
  await expect(page.getByText(`Approved through ${us(iso(last))}; the account is closed through it.`)).toBeVisible();
  const approved = page.getByRole("table", { name: "Approved" }).getByRole("row", {
    name: `${us(iso(first))} to ${us(iso(last))} $4,700.00 $4,700.00 Approver E2E`,
  });
  await expect(approved).toBeVisible();
});

test("staff bring a roster in, open an account with its old balances and a check not cleared, sign off and match the opening", async ({
  page,
}) => {
  const now = new Date();
  const cutover = new Date(Date.UTC(now.getUTCFullYear(), now.getUTCMonth() - 1, 1));
  const iso = (d: Date) => d.toISOString().slice(0, 10);
  const before = iso(new Date(cutover.getTime() - 86_400_000));
  const file = (name: string, text: string) => ({ name, mimeType: "text/csv", buffer: Buffer.from(text) });

  await page.goto(`/invite#${seed.switch_invite}`);
  await page.getByLabel("Choose a password (12 characters or more)").fill(PASSWORD);
  await page.getByLabel("The same password again").fill(PASSWORD);
  await page.getByRole("button", { name: "Set password" }).click();
  await page.getByLabel("Code").fill(totp((await page.locator("code").textContent()) ?? ""));
  await page.getByRole("button", { name: "Finish" }).click();
  const sections = page.getByRole("navigation", { name: "Sections" });
  await sections.getByRole("link", { name: "Settings", exact: true }).click();
  await page.getByRole("list", { name: "Settings" }).getByRole("link", { name: "Import from another PMS" }).click();

  await page.getByLabel("CSV file").setInputFiles(file("owners.csv", "Name\nOwner Cutover E2E\n"));
  await page.getByRole("button", { name: "Import" }).click();
  await expect(page.getByText("1 added.")).toBeVisible();
  await page.getByRole("combobox", { name: "File of", exact: true }).selectOption("properties");
  await page
    .getByLabel("CSV file")
    .setInputFiles(file("properties.csv", "Property,Owner,Bank account\nE2E Property 8,Owner Cutover E2E,E2E cutover trust account\n"));
  await page.getByRole("button", { name: "Import" }).click();
  await expect(page.getByText("1 added.")).toBeVisible();

  const card = page.locator("section", { hasText: "Cutover: E2E cutover trust account" });
  await card.getByLabel("Cutover date").fill(iso(cutover));
  await card.getByLabel("Old system's book cash").fill("900.00");
  await card
    .getByLabel("Opening balances (CSV)")
    .setInputFiles(file("balances.csv", "Kind,Owner,Property,Amount\nowner,Owner Cutover E2E,E2E Property 8,900.00\n"));
  await card.getByRole("button", { name: "Open with balances" }).click();
  await expect(card).toContainText("Opened with 1 balance.");
  await card
    .getByLabel("Checks and deposits not cleared (CSV)")
    .setInputFiles(file("items.csv", `Date,Amount,Description,Reference\n${before},-150.00,Check to Vendor E2E,2001\n`));
  await card.getByRole("button", { name: "Add items" }).click();
  await expect(card).toContainText("1 item carried over.");
  await expect(card).toContainText("The bank's statement should open at $1,050.00.");
  await card.getByRole("button", { name: "Sign off the tie-out" }).click();
  await expect(card).toContainText("Signed off. The items carried over are frozen.");
  await expect(card).toContainText("Switch E2E at ");

  await sections.getByRole("link", { name: "Banking", exact: true }).click();
  await page.getByRole("row", { name: "E2E cutover trust account", exact: true }).getByRole("link").click();
  await expect(page.getByRole("heading", { level: 1, name: "E2E cutover trust account" })).toBeVisible();
  await page.getByLabel("Statement file (CSV or OFX)").setInputFiles(file("statement.csv", "Date,Description,Amount\n"));
  await page.getByLabel("First day").fill(iso(cutover));
  await page.getByLabel("Last day").fill(iso(cutover));
  await page.getByLabel("Opening balance", { exact: true }).fill("1050.00");
  await page.getByRole("button", { name: "Import statement" }).click();
  await expect(page.getByText(`Imported 1 line, ${us(iso(cutover))} to ${us(iso(cutover))}.`)).toBeVisible();
  await expect(page.getByText(/balance brought forward should be \$1,050.00/)).toBeVisible();
  await page.getByRole("button", { name: "Match the opening" }).click();
  await expect(page.getByText("The opening is matched.")).toBeVisible();
  // Carried by the opening match, the check waits for the bank line that clears it.
  await expect(page.getByLabel(`${us(before)} Check to Vendor E2E (2001), from before the cutover: ($150.00)`)).toBeVisible();
  const carried = page.getByRole("table", { name: "Reconciling items" }).getByRole("row", {
    name: `outstanding withdrawal ${before} Check to Vendor E2E (2001), from before the cutover ($150.00)`,
  });
  await expect(carried).toBeVisible();
});

test("the demo opens on last month waiting for its manager, who approves it and sees on the Dashboard what else waits; its owner reads three properties", async ({
  page,
}) => {
  const now = new Date();
  const first = new Date(Date.UTC(now.getUTCFullYear(), now.getUTCMonth() - 1, 1));
  const last = new Date(Date.UTC(now.getUTCFullYear(), now.getUTCMonth(), 0));
  const iso = (d: Date) => d.toISOString().slice(0, 10);

  await page.goto(`/invite#${seed.demo_manager_invite}`);
  await page.getByLabel("Choose a password (12 characters or more)").fill(PASSWORD);
  await page.getByLabel("The same password again").fill(PASSWORD);
  await page.getByRole("button", { name: "Set password" }).click();
  await page.getByLabel("Code").fill(totp((await page.locator("code").textContent()) ?? ""));
  await page.getByRole("button", { name: "Finish" }).click();
  await page.getByRole("navigation", { name: "Sections" }).getByRole("link", { name: "Banking", exact: true }).click();
  await page.getByRole("row", { name: "Demo operating trust account", exact: true }).getByRole("link").click();
  await expect(page.getByRole("heading", { level: 1, name: "Demo operating trust account" })).toBeVisible();

  // The bookkeeper prepared last month; the roof repair's check hasn't cleared the bank.
  await expect(page.getByRole("table", { name: "Reconciling items" })).toContainText("Bill RF-2207 paid");
  await expect(page.getByLabel("Prepared")).toContainText(
    `Prepared through ${us(iso(last))} by Demo Bookkeeper: bank $2,379.20, books $1,729.20.`,
  );
  await page.getByRole("button", { name: `Approve through ${us(iso(last))}` }).click();
  await expect(page.getByText(`Approved through ${us(iso(last))}; the account is closed through it.`)).toBeVisible();
  const approved = page.getByRole("table", { name: "Approved" }).getByRole("row", {
    name: `${us(iso(first))} to ${us(iso(last))} $2,379.20 $1,729.20 Demo Manager`,
  });
  await expect(approved).toBeVisible();

  // Today lists the rest that waits: the bookkeeper's journal entry, the roofer's insurance
  // running out, last month's fees still in trust (the month before last's were taken out),
  // and a lease that requires renters insurance with none on file. Each trust bank account
  // has its broker, so signers have nothing to say.
  await page.getByRole("navigation", { name: "Sections" }).getByRole("link", { name: "Dashboard", exact: true }).click();
  const thisMonth = new Date(Date.UTC(now.getUTCFullYear(), now.getUTCMonth(), 1));
  await expect(page.getByRole("region", { name: "Journal entries waiting" })).toContainText(
    `Reserve moved to Birch Flats, as Demo Owner A asked: $25.00, dated ${us(iso(thisMonth))}, proposed by Demo Bookkeeper`,
  );
  await expect(page.getByRole("region", { name: "Vendor insurance" })).toContainText(
    "Demo Roofing Co, general liability: expires in 20 days",
  );
  await expect(page.getByRole("region", { name: "Company fees in trust" })).toContainText(
    `Demo operating trust account: $779.20 of the company's fees, the oldest since ${us(iso(last))}`,
  );
  await expect(page.getByRole("region", { name: "Renters insurance" })).toHaveText(
    /Unit 1, Aspen Row \(Drew Sample\): renters insurance none on file/,
  );
  await expect(page.getByRole("region", { name: "Trust account signers" })).toHaveCount(0);
  await page.getByRole("button", { name: "Sign out" }).click();

  await page.goto(`/invite#${seed.demo_owner_invite}`);
  await page.getByLabel("Choose a password (12 characters or more)").fill(PASSWORD);
  await page.getByLabel("The same password again").fill(PASSWORD);
  await page.getByRole("button", { name: "Set password" }).click();
  await expect(page.getByRole("heading", { name: "Your properties" })).toBeVisible();
  const holdings = page.locator("section", { hasText: "What each property holds today" });
  for (const name of ["Birch Flats", "Cedar House", "Elm Court"]) {
    await expect(holdings.getByRole("row", { name: new RegExp(name) })).toContainText("$250.00");
  }
});
