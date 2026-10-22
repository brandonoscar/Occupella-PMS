// The portal in a real browser, against the API and a database seeded by e2e/seed.py.
import { readFileSync } from "node:fs";
import { expect, type Locator, type Page, test } from "@playwright/test";
import { expectAccessible } from "./a11y";
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
  accessibility: { pmc_id: string; manager: string; owner: string; tenant: string };
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
  await expectAccessible(page);
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

  // The invite is on the tenant's own page; Logins lists it, read only.
  await page.getByRole("navigation", { name: "Sections" }).getByRole("link", { name: "Tenants", exact: true }).click();
  await page.getByRole("row", { name: /^Tenant E2E/ }).getByRole("link", { name: "Tenant E2E" }).click();
  const portal = page.locator("section", { has: page.getByRole("heading", { level: 2, name: "Portal", exact: true }) });
  await expect(portal).toContainText("None");
  await portal.getByLabel("Email to sign in with").fill("tenant.e2e@example.com");
  await portal.getByRole("button", { name: "Invite to the portal" }).click();
  await expect(portal.getByLabel("Invite link, Tenant E2E")).toHaveValue(/\/invite#/);
  await expect(portal).toContainText("Invited");
  await page.getByRole("navigation", { name: "Sections" }).getByRole("link", { name: "Settings", exact: true }).click();
  await page.getByRole("list", { name: "Settings" }).getByRole("link", { name: "Logins" }).click();
  await page.getByLabel("Status").selectOption({ label: "Invited" });
  await expect(page.getByRole("row", { name: "Tenant E2E" })).toContainText("tenant.e2e@example.com");

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

  await staff.getByRole("row", { name: "Application from Applicant E2E" }).click();
  await staff.getByRole("button", { name: "Screen" }).click();
  await expect(staff.getByRole("list", { name: "Screenings" })).toContainText("Recommends declining");
  await staff.getByRole("button", { name: "Reject" }).click();
  const rejecting = staff.getByRole("dialog", { name: "Reject" });
  await expect(rejecting.getByLabel("Adverse action reasons, one a line")).toHaveValue(
    "Household income is less than twice the rent",
  );
  await rejecting.getByRole("button", { name: "Reject" }).click();
  await expect(staff.getByText("Rejected: Applicant E2E.")).toBeVisible();
  await expect(staff.getByTestId("status")).toHaveText("Rejected");
  const notice = await staff.request.get(
    (await staff.getByRole("link", { name: "Notice (PDF)" }).getAttribute("href")) ?? "",
  );
  expect(notice.headers()["content-type"]).toBe("application/pdf");

  const path = new URL(statusUrl).pathname + new URL(statusUrl).hash;
  await page.goto(path);
  await expect(page.getByTestId("application-status")).toContainText("Declined");
});

test("staff draft a lease with a deposit and a monthly charge and send it, void a renewal, send another; the signers sign on the PMS's own page; both become leases", async ({
  page,
  browser,
}) => {
  await page.goto(`/invite#${seed.offers_invite}`);
  await page.getByLabel("Choose a password (12 characters or more)").fill(PASSWORD);
  await page.getByLabel("The same password again").fill(PASSWORD);
  await page.getByRole("button", { name: "Set password" }).click();
  await page.getByLabel("Code").fill(totp((await page.locator("code").textContent()) ?? ""));
  await page.getByRole("button", { name: "Finish" }).click();
  const draftLeases = async () => {
    await page.getByRole("navigation", { name: "Sections" }).getByRole("link", { name: "Draft leases", exact: true }).click();
    await expect(page.getByRole("heading", { level: 1, name: "Draft leases" })).toBeVisible();
  };
  const newDraft = async (forWhom: string) => {
    await draftLeases();
    await page.getByRole("button", { name: "New draft lease" }).click();
    const panel = page.getByRole("dialog", { name: "New draft lease" });
    await panel.getByRole("combobox", { name: "For", exact: true }).selectOption({ label: forWhom });
    return panel;
  };
  const document = {
    name: "lease.pdf",
    mimeType: "application/pdf",
    buffer: Buffer.from("%PDF-1.4 a synthetic lease for the browser test"),
  };

  // A lease for the approved applicant, kept as a draft with its deposit and pet rent, then sent.
  let panel = await newDraft("Mover E2E for E2E Property 3, E2E Unit 4");
  await expect(panel.getByLabel("Starts")).toHaveValue(day(30));
  await panel.getByLabel("Security deposit (optional)").fill("1000.00");
  await panel.getByRole("button", { name: "Add a monthly charge" }).click();
  await panel.getByLabel("What for").fill("Pet rent");
  await panel.getByLabel("Amount").fill("25.00");
  await panel.getByLabel("Lease document (PDF, your own)").setInputFiles(document);
  await panel.getByRole("button", { name: "Save draft" }).click();
  await expect(page.getByRole("heading", { level: 1, name: "Lease for E2E Property 3, E2E Unit 4" })).toBeVisible();
  await expect(page.getByTestId("status")).toHaveText("Draft");
  await expect(page.getByLabel("Summary")).toContainText("Security deposit$1,000.00");
  await expect(page.getByRole("table", { name: "Monthly charges" })).toContainText("Pet rentFee$25.00");
  const pdf = await page.request.get(
    (await page.getByRole("link", { name: "Lease (PDF)" }).getAttribute("href")) ?? "",
  );
  expect(await pdf.body()).toEqual(document.buffer);
  await page.getByRole("button", { name: "Send", exact: true }).click();
  await expect(page.getByText("Sent to 1 signer.")).toBeVisible();
  // The server sends email here: the signer was emailed their own link (migration 0090).
  await expect(page.getByText(/Each was emailed their link: /)).toBeVisible();
  await expect(page.getByTestId("status")).toHaveText("Sent");
  const link = new URL((await page.getByTestId("signing-link").textContent()) ?? "");

  // The applicant signs on the PMS's own page (ADR 0008): they agree to sign electronically after
  // reading the disclosures, open the lease, type their name and draw their signature.
  const mover = await (await browser.newContext()).newPage();
  await mover.goto(link.pathname + link.hash);
  await expect(mover.getByText("For Mover E2E, as a tenant")).toBeVisible();
  await expect(mover.getByTestId("disclosure")).toContainText("asks you to receive and sign this lease electronically");
  await mover.getByLabel("I have read this and agree to receive and sign this lease electronically").check();
  await mover.getByRole("button", { name: "Continue" }).click();
  const opened = mover.waitForEvent("download");
  await mover.getByRole("button", { name: "Open the lease (PDF)" }).click();
  expect(readFileSync((await (await opened).path()) ?? "")).toEqual(document.buffer);
  const pad = mover.getByTestId("signature-pad");
  const box = (await pad.boundingBox())!;
  await mover.mouse.move(box.x + 20, box.y + box.height / 2);
  await mover.mouse.down();
  await mover.mouse.move(box.x + box.width / 2, box.y + 20, { steps: 8 });
  await mover.mouse.up();
  await mover.getByLabel("Type your name as it is on the lease (Mover E2E)").fill("Someone Else");
  await mover.getByLabel("I have read the lease and I sign it").check();
  await mover.getByRole("button", { name: "Sign the lease" }).click();
  await expect(mover.getByRole("alert")).toHaveText("type your name as it is on the lease");
  await mover.getByLabel("Type your name as it is on the lease (Mover E2E)").fill("mover e2e");
  await mover.getByRole("button", { name: "Sign the lease" }).click();
  await expect(mover.getByTestId("sign-status")).toContainText("You signed on");
  const kept = mover.waitForEvent("download");
  await mover.getByRole("button", { name: "Download the signing certificate (PDF)" }).click();
  expect(readFileSync((await (await kept).path()) ?? "").subarray(0, 5).toString()).toBe("%PDF-");

  // Staff see it signed at once, and download the certificate.
  await page.reload();
  await expect(page.getByTestId("progress")).toHaveText("Signed");
  const certificate = await page.request.get(
    (await page.getByRole("link", { name: "Signing certificate (PDF)" }).getAttribute("href")) ?? "",
  );
  expect(certificate.headers()["content-type"]).toBe("application/pdf");

  await page.getByRole("button", { name: "Activate lease" }).click();
  await expect(page.getByText(`E2E Property 3, E2E Unit 4 has its lease from ${us(day(30))}.`)).toBeVisible();
  await expect(page.getByTestId("status")).toHaveText("Lease active");
  // The lease owes the pet rent every month from its first day.
  await page.getByRole("link", { name: "Open the lease" }).click();
  await expect(page.getByRole("main")).toContainText("Pet rent");
  // Asking a deposit held nothing: the lease says what its signed offer asked and is still to
  // receive, and Record deposit starts from it (q036).
  const asked = page.getByTestId("deposit-asked");
  await expect(asked).toContainText("The signed lease asked $1,000.00: $0.00 held, $1,000.00 still to receive");
  await page.locator("section", { has: asked }).getByRole("button", { name: "Record deposit" }).click();
  const received = page.getByRole("dialog", { name: "Record deposit" });
  await expect(received.getByLabel("Deposit received", { exact: true })).toHaveValue("1000.00");
  await received.getByRole("button", { name: "Record deposit" }).click();
  await expect(asked).toContainText("The signed lease asked $1,000.00: all held");

  // A renewal kept as a draft, then voided: it is never sent.
  panel = await newDraft("E2E Property 3, E2E Unit 3: Renewer E2E");
  await panel.getByLabel("Starts").fill(day(45));
  await panel.getByLabel("Lease document (PDF, your own)").setInputFiles(document);
  await panel.getByRole("button", { name: "Save draft" }).click();
  await expect(page.getByTestId("status")).toHaveText("Draft");
  await page.getByRole("button", { name: "Actions for E2E Property 3, E2E Unit 3" }).click();
  await page.getByRole("menuitem", { name: "Void" }).click();
  const voiding = page.getByRole("dialog", { name: "Void" });
  await voiding.getByLabel("Why (optional)").fill("Wrong start day");
  await voiding.getByRole("button", { name: "Void it" }).click();
  await expect(page.getByTestId("status")).toHaveText("Void");
  await expect(page.getByTestId("void")).toContainText("Wrong start day");
  await draftLeases();
  await page.getByRole("tab", { name: "Void", exact: true }).click();
  await expect(page.getByRole("row", { name: "Renewal for E2E Property 3, E2E Unit 3" }).getByTestId("status")).toHaveText("Void");

  // The renewal sent at once; the tenant signs it from their portal.
  panel = await newDraft("E2E Property 3, E2E Unit 3: Renewer E2E");
  await panel.getByLabel("Starts").fill(day(60));
  await panel.getByLabel("Lease document (PDF, your own)").setInputFiles(document);
  await panel.getByRole("button", { name: "Send for signing" }).click();
  await expect(page.getByTestId("status")).toHaveText("Sent");
  await expect(page.getByText("0 of 1 signed", { exact: true })).toBeVisible();
  // Staff can make each signer a new link.
  await page.getByRole("button", { name: "Resend" }).click();
  await expect(page.getByTestId("signing-link")).toContainText("/sign#");

  const tenant = await (await browser.newContext()).newPage();
  await tenant.goto(`/invite#${seed.renewer_invite}`);
  await tenant.getByLabel("Choose a password (12 characters or more)").fill(PASSWORD);
  await tenant.getByLabel("The same password again").fill(PASSWORD);
  await tenant.getByRole("button", { name: "Set password" }).click();
  const offered = tenant.getByRole("listitem", { name: "Renewal of E2E Unit 3" });
  await expect(offered).toHaveCount(1); // the void one is not the tenant's to see
  await expect(offered).toContainText(`${us(day(60))} – Month to month, $1,000.00 a month`);
  const read = await tenant.request.get(
    (await offered.getByRole("link", { name: "Read the lease (PDF)" }).getAttribute("href")) ?? "",
  );
  expect(await read.body()).toEqual(document.buffer);
  // The tenant first asks to sign on paper: that link signs nothing more, and staff see it.
  await offered.getByRole("button", { name: "Sign" }).click();
  await tenant.getByRole("button", { name: "I'd rather sign on paper" }).click();
  await tenant.getByRole("button", { name: "Sign on paper", exact: true }).click();
  await expect(tenant.getByText(/You asked to sign on paper/)).toBeVisible();
  await page.reload();
  await expect(page.getByTestId("progress")).toHaveText("Asked for paper");
  // They change their mind: the portal gives them a new link, and they sign through it.
  await tenant.goto("/");
  await tenant.getByRole("listitem", { name: "Renewal of E2E Unit 3" }).getByRole("button", { name: "Sign" }).click();
  await tenant.getByLabel("I have read this and agree to receive and sign this lease electronically").check();
  await tenant.getByRole("button", { name: "Continue" }).click();
  const read2 = tenant.waitForEvent("download");
  await tenant.getByRole("button", { name: "Open the lease (PDF)" }).click();
  await read2;
  await tenant.getByLabel("Type your name as it is on the lease (Renewer E2E)").fill("Renewer E2E");
  await tenant.getByLabel("I have read the lease and I sign it").check();
  await tenant.getByRole("button", { name: "Sign the lease" }).click();
  await expect(tenant.getByTestId("sign-status")).toBeVisible();
  await tenant.goto("/");
  await expect(tenant.getByTestId("renewal-signed")).toContainText("You signed");

  await page.reload();
  await expect(page.getByText("1 of 1 signed", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "Activate lease" }).click();
  await expect(page.getByTestId("status")).toHaveText("Lease active");
  // Staff keep the renewal's pet addendum on the lease's own Files tab.
  await page.getByRole("link", { name: "Open the lease" }).click();
  await page.getByRole("tab", { name: "Files" }).click();
  await page.getByRole("combobox", { name: "Category" }).selectOption({ label: "Lease document" });
  await page.getByLabel("Lease document file").setInputFiles({
    name: "addendum.pdf",
    mimeType: "application/pdf",
    buffer: Buffer.from("%PDF-1.4 a synthetic pet addendum"),
  });
  await page.getByLabel("Title").fill("Pet addendum");
  await page.getByRole("button", { name: "Upload", exact: true }).click();
  await expect(page.getByText("Uploaded Pet addendum.")).toBeVisible();
  await draftLeases();
  await page.getByRole("tab", { name: "Leases made", exact: true }).click();
  for (const row of ["Lease for E2E Property 3, E2E Unit 4", "Renewal for E2E Property 3, E2E Unit 3"]) {
    await expect(page.getByRole("row", { name: row }).getByTestId("status")).toHaveText("Lease active");
  }
  await tenant.reload();
  await expect(tenant.getByRole("heading", { name: "Welcome, Renewer E2E" })).toBeVisible();
  await expect(tenant.getByRole("heading", { name: "Lease renewal" })).toHaveCount(0);
  // The tenant keeps the renewal as signed, its signing certificate and its addendum (q120).
  await tenant.getByRole("combobox", { name: /^Lease/ }).first().selectOption({ label: `E2E Property 3, E2E Unit 3, from ${us(day(60))} (to come)` });
  const files = tenant.getByRole("list", { name: "Your lease's files" });
  const fetched = async (name: string) =>
    (await tenant.request.get((await files.getByRole("link", { name }).getAttribute("href")) ?? "")).body();
  expect(await fetched("Lease renewal (as signed, PDF)")).toEqual(document.buffer);
  expect((await fetched("Signing certificate")).subarray(0, 5).toString()).toBe("%PDF-");
  expect((await fetched("Pet addendum")).toString()).toBe("%PDF-1.4 a synthetic pet addendum");
});

test("staff set up a property from nothing where each thing lives, lease a unit and see it on the rent roll", async ({ page }) => {
  await page.goto(`/invite#${seed.setup_invite}`);
  await page.getByLabel("Choose a password (12 characters or more)").fill(PASSWORD);
  await page.getByLabel("The same password again").fill(PASSWORD);
  await page.getByRole("button", { name: "Set password" }).click();
  await page.getByLabel("Code").fill(totp((await page.locator("code").textContent()) ?? ""));
  await page.getByRole("button", { name: "Finish" }).click();
  const go = (name: string) =>
    page.getByRole("navigation", { name: "Sections" }).getByRole("link", { name, exact: true }).click();

  // An owner from Owners, then their property from Properties, which opens its page.
  await go("Owners");
  await page.getByRole("button", { name: "Add owner" }).click();
  await page.getByRole("dialog", { name: "Add owner" }).getByLabel("Name").fill("Owner Setup E2E");
  await page.getByRole("dialog", { name: "Add owner" }).getByRole("button", { name: "Add owner" }).click();
  await expect(page.getByRole("heading", { level: 1, name: "Owner Setup E2E" })).toBeVisible();
  await go("Properties");
  await page.getByRole("button", { name: "Add property" }).click();
  const adding = page.getByRole("dialog", { name: "Add property" });
  await adding.getByLabel("Property name").fill("E2E Property 9");
  await adding.getByLabel("Owner").selectOption({ label: "Owner Setup E2E" });
  await adding.getByLabel("Held in").selectOption({ label: "E2E operating trust account" });
  await adding.getByRole("button", { name: "Add property" }).click();
  await expect(page.getByRole("heading", { level: 1, name: "E2E Property 9" })).toBeVisible();
  await expect(page.getByRole("table", { name: "Owners" }).getByRole("row", { name: "Owner Setup E2E" })).toContainText("No terms in force");

  // Its unit, from its page; then a lease already in place, from the unit's page.
  await page.getByRole("button", { name: "Add unit" }).click();
  await page.getByRole("dialog", { name: "Add unit" }).getByLabel("Unit name").fill("Unit 9A");
  await page.getByRole("dialog", { name: "Add unit" }).getByRole("button", { name: "Add unit" }).click();
  await expect(page.getByText("Added Unit 9A.")).toBeVisible();
  await page.getByRole("row", { name: "Unit 9A" }).getByRole("link", { name: "Unit 9A" }).click();
  await page.getByRole("button", { name: "Add lease in place" }).click();
  const lease = page.getByRole("dialog", { name: "Add lease" });
  await lease.getByLabel("Tenant's name").fill("Tenant Setup E2E");
  await lease.getByRole("button", { name: "Add tenant" }).click();
  await expect(lease.getByLabel("Tenant Setup E2E")).toBeChecked();
  const today = day(0);
  await lease.getByLabel("Starts").fill(today);
  await lease.getByLabel("Rent a month").fill("1100.00");
  await lease.getByRole("button", { name: "Add lease" }).click();
  await expect(page.getByRole("row", { name: "Tenant Setup E2E" })).toContainText(`${us(today)} – Month to month`);

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
  await expect(row.getByTestId("balance")).toHaveText("$800.00");
  await row.getByRole("link", { name: "E2E Unit 5" }).click();
  await expect(page.getByRole("heading", { level: 1, name: /^E2E Property 3 · E2E Unit 5 · / })).toBeVisible();
  const lease = page.getByRole("main");
  const owed = lease.getByTestId("balance-due");
  await expect(owed).toHaveText("$800.00");
  await page.getByRole("button", { name: "Receive payment", exact: true }).click();
  const pay = page.getByRole("dialog", { name: "Receive payment" });
  await pay.getByLabel("Payment received").fill("1000.00");
  await pay.getByLabel("Check or reference").fill("Money order 9");
  await pay.getByRole("button", { name: "Record payment" }).click();
  await expect(lease).toContainText("$800.00 paid what is owed; $200.00 held as prepaid rent.");
  await expect(owed).toHaveText("$0.00");
  await expect(lease.getByTestId("prepaid")).toHaveText("$200.00");
  await page.getByRole("tab", { name: "Ledger", exact: true }).click();
  const payment = lease.getByRole("table", { name: "Payments received" }).getByRole("row", { name: /Money order 9/ });
  await expect(payment).toBeVisible();

  // A bounce is asked for, with the day it came back (today by default), before it is recorded.
  await payment.getByRole("button", { name: /^Actions for / }).click();
  await page.getByRole("menuitem", { name: "It bounced" }).click();
  await payment.getByRole("button", { name: "Record the $1,000.00 bounce" }).click();
  // The whole $1,000.00 comes back: the $800.00 it paid is owed again, and its $200.00 of
  // prepaid rent is gone.
  await expect(lease).toContainText("The $1,000.00 payment bounced and is undone.");
  await expect(owed).toHaveText("$800.00");
  await expect(payment.getByText("Bounced", { exact: true })).toBeVisible();
  await page.getByRole("tab", { name: "Summary", exact: true }).click();
  await expect(lease.getByTestId("prepaid")).toHaveText("$0.00");
});

test("staff record a vendor's bill with its invoice, record the owner's approval, set it aside and pay it on its page, then draw the rest", async ({
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
  const recording = page.getByRole("dialog", { name: "Record bill" });
  await recording.getByRole("combobox", { name: "Vendor", exact: true }).selectOption({ label: "Vendor E2E" });
  await recording
    .getByRole("combobox", { name: "Owner and property", exact: true })
    .selectOption({ label: "Payee E2E, E2E Property 7" });
  await recording.getByLabel("Ref #").fill("E2E-INV-1");
  await recording.getByLabel("Amount", { exact: true }).fill("300.00");
  await recording.getByLabel("Memo").fill("Gutter repair");
  await recording.getByLabel("Invoice (PDF, JPEG or PNG)").setInputFiles({
    name: "invoice-e2e.pdf", mimeType: "application/pdf", buffer: Buffer.from("%PDF-1.4 synthetic invoice\n"),
  });
  await recording.getByRole("button", { name: "Record bill" }).click();
  await expect(page.getByText("Bill E2E-INV-1 recorded.")).toBeVisible();

  // No agreement: every bill needs the owner's approval first. Its own page takes each step.
  const bill = page.getByRole("row", { name: "Vendor E2E E2E-INV-1", exact: true });
  await expect(bill.getByTestId("status")).toHaveText("Awaiting owner approval");
  await bill.getByRole("cell").first().click();
  await expect(page.getByRole("heading", { level: 1, name: "Vendor E2E E2E-INV-1" })).toBeVisible();
  await expect(page.getByRole("list", { name: "Files, Vendor E2E E2E-INV-1" })).toContainText("invoice-e2e");
  await page.getByRole("button", { name: "Record approval" }).click();
  await page.getByRole("dialog", { name: "Record approval" }).getByLabel("Approved by").fill("Payee E2E");
  await page.getByRole("dialog", { name: "Record approval" }).getByRole("button", { name: "Record approval" }).click();
  await expect(page.getByTestId("status")).toHaveText("Approved");
  await page.getByRole("button", { name: "Set aside" }).click();
  await page.getByRole("dialog", { name: "Set aside" }).getByRole("button", { name: "Set aside" }).click();
  await expect(page.getByTestId("status")).toHaveText("Set aside");
  await page.getByRole("button", { name: "Pay", exact: true }).click();
  await page.getByRole("dialog", { name: "Pay" }).getByRole("button", { name: "Pay $300.00" }).click();
  await page.getByRole("alertdialog").getByRole("button", { name: "Confirm" }).click();
  await expect(page.getByTestId("status")).toHaveText("Paid");
  await expect(page.getByRole("list", { name: "History" })).toContainText("Paid by check");

  // Pay owners pays the rest out: everything available, by check.
  await sections.getByRole("link", { name: "Pay owners" }).click();
  const payee = page.getByRole("row", { name: "Payee E2E, E2E Property 7", exact: true });
  await expect(payee.getByRole("cell").nth(6)).toHaveText("$700.00");
  await payee.getByLabel("Pay Payee E2E, E2E Property 7").check();
  await page.getByRole("button", { name: "Pay $700.00" }).click();
  await page.getByRole("alertdialog").getByRole("button", { name: "Confirm" }).click();
  await expect(page.getByRole("main")).toContainText("1 payout of $700.00 recorded, by check.");
  await expect(payee.getByRole("cell").nth(6)).toHaveText("$0.00");
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
  // The day its deposit is due back (the strictest deadline found), and a receipt for what it keeps
  // (migration 0120): over $125 with none on file, it asks for one.
  await expect(card.getByText(/^(Return by|Was due back) \d\d\/\d\d\/\d{4}$/)).toBeVisible();
  const fees = card.getByRole("list", { name: "Fees and receipts, E2E Property 8 / E2E Unit 8: Leaver E2E" });
  await expect(fees.getByText("Receipt needed")).toBeVisible();
  await fees.getByLabel("Receipt for Carpet cleaning").setInputFiles({
    name: "carpet-invoice.pdf", mimeType: "application/pdf", buffer: Buffer.from("%PDF-1.4\n% synthetic invoice\n"),
  });
  await fees.getByRole("button", { name: "Add receipt" }).click();
  await expect(card.getByText("Kept carpet-invoice as a receipt for Carpet cleaning.")).toBeVisible();
  await expect(fees.getByRole("link", { name: "carpet-invoice" })).toBeVisible();
  await expect(fees.getByText("Receipt needed")).toHaveCount(0);

  await card.getByRole("button", { name: "Return the deposit" }).click();
  await page.getByRole("alertdialog").getByRole("button", { name: "Confirm" }).click();
  await expect(card.getByText("Returned $750.00 to the tenants; the rest paid what the lease owed.")).toBeVisible();
  await expect(card.getByLabel("Returned, E2E Property 8 / E2E Unit 8: Leaver E2E")).toContainText(
    "kept $150.00 of $900.00, $750.00 back to the tenants",
  );
  // Each tenant's statement: a letter with the receipt, to print and mail or to email.
  const statement = await page.request.get(
    (await card.getByRole("link", { name: "Statement for Leaver E2E (PDF)" }).getAttribute("href")) ?? "",
  );
  expect(statement.status()).toBe(200);
  expect(statement.headers()["content-type"]).toBe("application/pdf");
  await card.getByRole("button", { name: "Email the statement" }).click();
  await expect(card.getByText(/^(Emailed Leaver E2E at .+\.|No email on file for Leaver E2E: add one on their page\.)$/)).toBeVisible();
});

test("staff keep an owner's document and a property's, and the owner downloads them from the portal", async ({ page, browser }) => {
  await page.goto(`/invite#${seed.documents_invite}`);
  await page.getByLabel("Choose a password (12 characters or more)").fill(PASSWORD);
  await page.getByLabel("The same password again").fill(PASSWORD);
  await page.getByRole("button", { name: "Set password" }).click();
  await page.getByLabel("Code").fill(totp((await page.locator("code").textContent()) ?? ""));
  await page.getByRole("button", { name: "Finish" }).click();
  await page.getByRole("navigation", { name: "Sections" }).getByRole("link", { name: "Files", exact: true }).click();

  const pdf = Buffer.from("%PDF-1.4\n% synthetic management agreement\n");
  await page.getByRole("button", { name: "Upload" }).click();
  const panel = page.getByRole("dialog", { name: "Upload" });
  await panel.getByLabel("File").setInputFiles({ name: "agreement-2026.pdf", mimeType: "application/pdf", buffer: pdf });
  await expect(panel.getByLabel("Name")).toHaveValue("agreement-2026");
  await panel.getByLabel("Name").fill("Management agreement 2026");
  await panel.getByRole("combobox", { name: "Attached to" }).selectOption("Owner");
  await panel.getByRole("combobox", { name: "Owner" }).selectOption({ label: "Owner E2E" });
  await expect(panel.getByRole("combobox", { name: "Category" })).toHaveValue("owner_document");
  await panel.getByRole("button", { name: "Upload" }).click();
  await expect(page.getByText("Uploaded Management agreement 2026.")).toBeVisible();
  const kept = page.getByRole("table", { name: "Files" }).getByRole("row", { name: "Management agreement 2026" });
  await expect(kept).toContainText("Owner E2E");
  await expect(kept).toContainText("Owner document");

  // A file about one of the owner's properties (q046): the owner reads it too.
  await page.getByRole("button", { name: "Upload" }).click();
  const another = page.getByRole("dialog", { name: "Upload" });
  await another.getByLabel("File").setInputFiles({ name: "roof-inspection.pdf", mimeType: "application/pdf", buffer: pdf });
  await another.getByLabel("Name").fill("Roof inspection");
  await another.getByRole("combobox", { name: "Attached to" }).selectOption("Property");
  await another.getByRole("combobox", { name: "Property" }).selectOption({ label: "E2E Property 1" });
  await expect(another.getByRole("combobox", { name: "Category" })).toHaveValue("property_document");
  await another.getByRole("button", { name: "Upload" }).click();
  await expect(page.getByText("Uploaded Roof inspection.")).toBeVisible();

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
  const theirs = owner.getByRole("list", { name: "Your documents" });
  await expect(theirs).toContainText("E2E Property 1");
  const roof = await owner.request.get((await theirs.getByRole("link", { name: "Roof inspection" }).getAttribute("href")) ?? "");
  expect(roof.status()).toBe(200);
  expect(roof.headers()["content-type"]).toBe("application/pdf");
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
  await page.getByRole("tab", { name: "Statements" }).click();
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

  await page.getByRole("tab", { name: "Reconciliations" }).click();
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
  await page.getByRole("tab", { name: "Statements" }).click();
  await page.getByLabel("Statement file (CSV or OFX)").setInputFiles(file("statement.csv", "Date,Description,Amount\n"));
  await page.getByLabel("First day").fill(iso(cutover));
  await page.getByLabel("Last day").fill(iso(cutover));
  await page.getByLabel("Opening balance", { exact: true }).fill("1050.00");
  await page.getByRole("button", { name: "Import statement" }).click();
  await expect(page.getByText(`Imported 1 line, ${us(iso(cutover))} to ${us(iso(cutover))}.`)).toBeVisible();
  await page.getByRole("tab", { name: "Reconciliations" }).click();
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
  // Its three-way reconciliation (bank, books and owners' ledgers on one page) downloads from the row.
  const threeWay = approved.getByRole("link", {
    name: `Three-way reconciliation, ${us(iso(first))} to ${us(iso(last))}, PDF`,
  });
  const pdf = await page.request.get((await threeWay.getAttribute("href")) ?? "");
  expect(pdf.status()).toBe(200);
  expect((await pdf.body()).subarray(0, 5).toString()).toBe("%PDF-");

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
  // Each holds its reserve and this month's rent, which its tenants paid.
  const holdings = page.locator("section", { hasText: "What each property holds today" });
  await expect(holdings.getByRole("row").filter({ hasText: "Demo operating trust account" })).toHaveCount(3);
  for (const name of ["Birch Flats", "Cedar House", "Elm Court"]) {
    await expect(holdings.getByRole("row", { name: new RegExp(name) })).toContainText("Demo operating trust account");
  }
});

// The pages a list opens, one of each kind, from the list's own links; "then" follows a link
// from the page found before (a unit from its property's page).
const DETAILS: [string, RegExp][] = [
  ["/properties", /^\/properties\/[^/]+$/],
  ["then", /^\/units\/[^/]+$/],
  ["/tenants", /^\/tenants\/[^/]+$/],
  ["/owners", /^\/owners\/[^/]+$/],
  ["/rent-roll", /^\/leases\/[^/]+$/],
  ["/bills", /^\/bills\/[^/]+$/],
  ["/work-orders", /^\/work-orders\/[^/]+$/],
  ["/vendors", /^\/vendors\/[^/]+$/],
  ["/banking", /^\/bank-accounts\/[^/]+$/],
  ["/applicants", /^\/applicants\/[^/]+$/],
  ["/leads", /^\/leads\/[^/]+$/],
  ["/draft-leases", /^\/draft-leases\/[^/]+$/],
  ["/housing-authorities", /^\/housing-authorities\/[^/]+$/],
  ["/reports", /^\/reports\/[^/]+$/],
];

async function hrefs(page: Page, where: Locator = page.locator("body")): Promise<string[]> {
  return where.locator("a[href^='/']").evaluateAll((links) => links.map((a) => a.getAttribute("href") ?? ""));
}

async function scanned(page: Page, path: string): Promise<void> {
  await page.goto(path);
  await expect(page.getByRole("heading", { level: 1 }).first()).toBeVisible();
  await page.waitForLoadState("networkidle");
  await expectAccessible(page, path);
}

test("every page of the demo meets WCAG 2.2 AA as axe checks it: staff's, an owner's, a tenant's and the public ones", async ({
  browser,
  page,
}) => {
  test.setTimeout(600_000);
  const demo = seed.accessibility;
  for (const path of [`/sign-in?pmc=${demo.pmc_id}`, "/forgot-password", `/listings?pmc=${demo.pmc_id}`]) {
    await scanned(page, path);
  }

  await page.goto(`/invite#${demo.manager}`);
  await expectAccessible(page, "the invite");
  await page.getByLabel("Choose a password (12 characters or more)").fill(PASSWORD);
  await page.getByLabel("The same password again").fill(PASSWORD);
  await page.getByRole("button", { name: "Set password" }).click();
  await expect(page.locator("code")).toBeVisible();
  await expectAccessible(page, "the authenticator step");
  await page.getByLabel("Code").fill(totp((await page.locator("code").textContent()) ?? ""));
  await page.getByRole("button", { name: "Finish" }).click();

  // Every page in staff's menu and in Settings, then one page of each kind a list opens. Each
  // list is read once it shows, since reading links doesn't wait for them.
  const sections = page.getByRole("navigation", { name: "Sections" });
  await expect(sections.getByRole("link", { name: "Dashboard" })).toBeVisible();
  const menu = await hrefs(page, sections);
  await page.goto("/settings");
  const settingsList = page.getByRole("list", { name: "Settings" });
  await expect(settingsList.getByRole("link").first()).toBeVisible();
  const settings = await hrefs(page, settingsList);
  const pages = [...new Set([...menu, ...settings, "/pay-bills", "/journal"])];
  expect(pages.length).toBeGreaterThan(30);
  for (const path of pages) await scanned(page, path);
  for (const [list, kind] of DETAILS) {
    if (list !== "then") await scanned(page, list);
    const found = (await hrefs(page, page.getByRole("main"))).find((href) => kind.test(href));
    expect(found, `a link from ${list} matching ${kind}`).toBeTruthy();
    await scanned(page, found!);
  }

  for (const who of [demo.owner, demo.tenant]) {
    const portal = await (await browser.newContext()).newPage();
    await portal.goto(`/invite#${who}`);
    await portal.getByLabel("Choose a password (12 characters or more)").fill(PASSWORD);
    await portal.getByLabel("The same password again").fill(PASSWORD);
    await portal.getByRole("button", { name: "Set password" }).click();
    await expect(portal.getByRole("navigation", { name: "Sections" })).toBeVisible();
    await portal.waitForLoadState("networkidle");
    await expectAccessible(portal, `the ${who === demo.owner ? "owner" : "tenant"}'s portal`);
    await portal.context().close();
  }
});
