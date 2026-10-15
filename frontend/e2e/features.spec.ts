// Each feature of the portal used end to end in a real browser, on a demo PMC of its own
// (src/pms/demo.py, seeded by e2e/seed.py): two months of rent, bills, fees, draws and month-end,
// last month prepared and waiting for its approval. ci/features.toml names the test that proves
// each feature, and tools/check_features.py keeps the two in step.
import { readFileSync } from "node:fs";
import { expect, type Page, test } from "@playwright/test";
import { totp } from "./totp";

type People = {
  pmc_id: string;
  manager: string;
  bookkeeper: string;
  owner_a: string;
  owner_b: string;
  taylor: string;
  drew: string;
  emails: Record<string, string>;
};
const seed: { features: Record<string, People> } = JSON.parse(
  readFileSync(new URL("./.seed.json", import.meta.url), "utf8"),
);
const PASSWORD = "a long synthetic passphrase";

// Days in UTC, as the server keeps them.
const today = () => new Date().toISOString().slice(0, 10);
const firstOfThisMonth = () => `${today().slice(0, 8)}01`;
const dayOfMonth = () => Number(today().slice(8, 10));
const firstOfMonthsAgo = (months: number) => {
  const [year, month] = today().split("-").map(Number) as [number, number];
  return new Date(Date.UTC(year, month - 1 - months, 1)).toISOString().slice(0, 10);
};

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

// A staff member takes their invite: a password, then the authenticator. Returns its secret.
async function joinAsStaff(page: Page, invite: string): Promise<string> {
  await page.goto(`/invite#${invite}`);
  await page.getByLabel("Choose a password (12 characters or more)").fill(PASSWORD);
  await page.getByLabel("The same password again").fill(PASSWORD);
  await page.getByRole("button", { name: "Set password" }).click();
  const secret = (await page.locator("code").textContent()) ?? "";
  await page.getByLabel("Code").fill(totp(secret));
  await page.getByRole("button", { name: "Finish" }).click();
  await expect(page.getByRole("navigation", { name: "Sections" })).toBeVisible();
  return secret;
}

// An owner or a tenant takes their invite: a password, and they are in.
async function joinPortal(page: Page, invite: string): Promise<void> {
  await page.goto(`/invite#${invite}`);
  await page.getByLabel("Choose a password (12 characters or more)").fill(PASSWORD);
  await page.getByLabel("The same password again").fill(PASSWORD);
  await page.getByRole("button", { name: "Set password" }).click();
  await expect(page.getByRole("navigation", { name: "Sections" })).toBeVisible();
}

// A part of a page: the section under its label (Card's h2).
const part = (page: Page, title: string) =>
  page.locator("section", { has: page.getByRole("heading", { level: 2, name: title, exact: true }) });

async function section(page: Page, name: string): Promise<void> {
  await page.getByRole("navigation", { name: "Sections" }).getByRole("link", { name }).click();
  await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
}

test("staff charge this month's rent and its late fees, and Today shows the rent that is late", async ({
  page,
}) => {
  const people = seed.features.rent!;
  await joinAsStaff(page, people.manager);

  // Last month waits for its approval on both trust bank accounts.
  await section(page, "Today");
  const monthEnd = page.getByRole("region", { name: "Month-end" });
  await expect(monthEnd).toContainText("Demo operating trust account");
  await expect(monthEnd).toContainText("Demo security deposit account");

  await section(page, "Rent");
  await page.getByLabel("Rent due on").fill(firstOfThisMonth());
  await page.getByRole("button", { name: "Charge rent" }).click();
  await expect(page.getByText("6 rents and 1 recurring charge charged.")).toBeVisible();
  // Once per lease and due date: the same run again charges nothing.
  await page.getByRole("button", { name: "Charge rent" }).click();
  await expect(page.getByText("0 rents and 0 recurring charges charged.")).toBeVisible();
  const lease = (name: string) => page.getByRole("listitem", { name });
  await expect(lease("Aspen Row, Unit 1").getByTestId("owed")).toHaveText("Owes $1,180.00");
  // Birch Flats Unit A owes its pet rent with its rent.
  await expect(lease("Birch Flats, Unit A").getByTestId("owed")).toHaveText("Owes $1,430.00");

  // Rent unpaid past five days' grace takes a $50.00 late fee, once.
  await page.getByLabel("Late fees as of").fill(today());
  await page.getByRole("button", { name: "Assess late fees" }).click();
  const late = dayOfMonth() > 6;
  await expect(page.getByText(`${late ? 6 : 0} late fees charged.`)).toBeVisible();
  await expect(lease("Aspen Row, Unit 1").getByTestId("owed")).toHaveText(
    late ? "Owes $1,230.00" : "Owes $1,180.00",
  );
  await page.getByRole("button", { name: "Assess late fees" }).click();
  await expect(page.getByText("0 late fees charged.")).toBeVisible();

  // Today lists the rent due before today and not paid, with the way to the rent page.
  await section(page, "Today");
  if (dayOfMonth() > 1) {
    const lateRent = page.getByRole("region", { name: "Late rent" });
    await expect(lateRent.getByRole("listitem")).toHaveCount(6);
    await lateRent.getByRole("link", { name: "Open Rent" }).click();
    await expect(page.getByRole("heading", { level: 1, name: "Rent" })).toBeVisible();
  } else {
    await expect(page.getByRole("region", { name: "Late rent" })).toHaveCount(0);
  }
});

test("staff add a fee, a credit and a monthly charge to a lease, take a deposit and prepaid rent, and a bounce undoes a payment and charges the NSF fee", async ({
  page,
}) => {
  const people = seed.features.recurring!;
  await joinAsStaff(page, people.manager);

  // The property's NSF fee, in Setup.
  await section(page, "Setup");
  const aspen = page.getByRole("listitem", { name: "Aspen Row" });
  await expect(aspen).toContainText("No NSF fee");
  await aspen.getByText("Add to Aspen Row").click();
  await aspen.getByLabel("NSF fee from").fill(firstOfThisMonth());
  await aspen.getByLabel("NSF fee", { exact: true }).fill("35.00");
  await aspen.getByRole("button", { name: "Set NSF fee" }).click();
  await expect(aspen).toContainText("NSF fee $35.00");

  await section(page, "Rent");
  const lease = page.getByRole("listitem", { name: "Aspen Row, Unit 1" });
  const owed = lease.getByTestId("owed");
  await expect(owed).toHaveText("Paid up");
  await lease.getByText("Record for Unit 1").click();

  // A one-off fee, then a credit that comes off it.
  await lease.getByLabel("Add a").selectOption("fee");
  await lease.getByLabel("Amount", { exact: true }).fill("25.00");
  await lease.getByLabel("What for").fill("Key replacement");
  await lease.getByRole("button", { name: "Add", exact: true }).click();
  await expect(lease).toContainText("Fee of $25.00 added.");
  await expect(owed).toHaveText("Owes $25.00");
  await lease.getByLabel("Add a").selectOption("credit");
  await lease.getByLabel("Amount", { exact: true }).fill("10.00");
  await lease.getByLabel("What for").fill("Goodwill");
  await lease.getByRole("button", { name: "Add", exact: true }).click();
  await expect(lease).toContainText("Credit of $10.00 added.");
  await expect(owed).toHaveText("Owes $15.00");

  // A payment over what is owed: the rest is the tenant's prepaid rent, used on the next fee.
  await lease.getByLabel("Payment received").fill("100.00");
  await lease.getByLabel("Check or reference").fill("Check 501");
  await lease.getByRole("button", { name: "Record payment" }).click();
  await expect(lease).toContainText("$15.00 paid what is owed; $85.00 held as prepaid rent.");
  await expect(owed).toHaveText("Paid up");
  await expect(lease).toContainText("Drew Sample: prepaid $85.00");
  // The payment is listed as it came in, not only the part that paid charges.
  await expect(lease.getByRole("row", { name: /Check 501/ })).toContainText("$100.00");
  await lease.getByLabel("Add a").selectOption("fee");
  await lease.getByLabel("Amount", { exact: true }).fill("30.00");
  await lease.getByLabel("What for").fill("Lock change");
  await lease.getByRole("button", { name: "Add", exact: true }).click();
  await expect(owed).toHaveText("Owes $30.00");
  await lease.getByRole("button", { name: "Use prepaid rent" }).click();
  await expect(lease).toContainText("$30.00 paid what is owed.");
  await expect(owed).toHaveText("Paid up");
  await expect(lease).toContainText("Drew Sample: prepaid $55.00");

  // A payment when nothing is owed is all prepaid rent; it is listed, and it can bounce.
  await lease.getByLabel("Payment received").fill("50.00");
  await lease.getByLabel("Check or reference").fill("Check 503");
  await lease.getByRole("button", { name: "Record payment" }).click();
  await expect(lease).toContainText("$50.00 held as prepaid rent.");
  await expect(lease).toContainText("Drew Sample: prepaid $105.00");
  const prepaidOnly = lease.getByRole("row", { name: /Check 503/ });
  await expect(prepaidOnly).toContainText("$50.00");
  await prepaidOnly.getByRole("button", { name: "It bounced" }).click();
  await prepaidOnly.getByRole("button", { name: "Record the bounce" }).click();
  await expect(lease).toContainText("The $50.00 payment bounced and is undone; a $35.00 NSF fee is charged.");
  await expect(lease).toContainText("Drew Sample: prepaid $55.00");
  await expect(owed).toHaveText("Owes $35.00");
  await lease.getByRole("button", { name: "Use prepaid rent" }).click();
  await expect(lease).toContainText("$35.00 paid what is owed.");
  await expect(owed).toHaveText("Paid up");

  // A monthly charge from next month, then stopped.
  const nextMonth = new Date(Date.UTC(Number(today().slice(0, 4)), Number(today().slice(5, 7)), 1))
    .toISOString()
    .slice(0, 10);
  await lease.getByLabel("Every month, a").selectOption("fee");
  await lease.getByLabel("Monthly amount").fill("40.00");
  await lease.getByLabel("First due on").fill(nextMonth);
  await lease.getByLabel("Monthly, for").fill("Parking");
  await lease.getByRole("button", { name: "Add every month" }).click();
  await expect(lease).toContainText("Parking of $40.00 added every month.");
  await expect(lease).toContainText(`Parking: $40.00 a month from ${nextMonth}`);
  await lease.getByRole("button", { name: "Stop" }).click();
  await lease.getByLabel("Stops on, Parking").fill(nextMonth);
  await lease.getByRole("button", { name: "Stop it" }).click();
  await expect(lease).toContainText(`Parking stops ${nextMonth}.`);
  await expect(lease).toContainText(`Parking: $40.00 a month from ${nextMonth}, stops ${nextMonth}`);

  // A deposit held for the tenant.
  await lease.getByLabel("Deposit received", { exact: true }).fill("100.00");
  await lease.getByRole("button", { name: "Record deposit" }).click();
  await expect(lease).toContainText("Deposit of $100.00 held.");
  await expect(lease).toContainText("deposit $1,280.00");

  // A payment that bounces is owed again, with the property's NSF fee.
  await lease.getByLabel("Add a").selectOption("fee");
  await lease.getByLabel("Amount", { exact: true }).fill("20.00");
  await lease.getByLabel("What for").fill("Late key return");
  await lease.getByRole("button", { name: "Add", exact: true }).click();
  await lease.getByLabel("Payment received").fill("20.00");
  await lease.getByLabel("Check or reference").fill("Check 502");
  await lease.getByRole("button", { name: "Record payment" }).click();
  await expect(lease).toContainText("$20.00 paid what is owed.");
  const payment = lease.getByRole("row", { name: /Check 502/ });
  await payment.getByRole("button", { name: "It bounced" }).click();
  await payment.getByRole("button", { name: "Record the bounce" }).click();
  await expect(lease).toContainText("The $20.00 payment bounced and is undone; a $35.00 NSF fee is charged.");
  await expect(owed).toHaveText("Owes $55.00");
  await expect(lease.getByRole("row", { name: /Check 502/ }).getByText("Bounced")).toBeVisible();
});

test("a bill over the owner's limit emails them; they decline one and approve another in the portal, and staff pay it", async ({
  browser,
  page,
}) => {
  const people = seed.features.bills!;
  // The owner has a portal login, so the PMS can email them.
  const owner = await (await browser.newContext()).newPage();
  await joinPortal(owner, people.owner_a);

  await joinAsStaff(page, people.manager);
  await section(page, "Bills");
  const enter = async (reference: string, amount: string, memo: string) => {
    await page.getByRole("combobox", { name: "Vendor", exact: true }).selectOption({ label: "Demo Plumbing Co" });
    await page
      .getByRole("combobox", { name: "Owner and property", exact: true })
      .selectOption({ label: "Demo Owner A, Birch Flats" });
    await page.getByLabel("Vendor's reference").fill(reference);
    await page.getByLabel("Amount", { exact: true }).fill(amount);
    await page.getByLabel("What for").fill(memo);
    await page.getByRole("button", { name: "Enter bill" }).click();
    await expect(page.getByText(`Bill ${reference} entered.`)).toBeVisible();
  };
  // Over Demo Owner A's $500.00 approval limit.
  await enter("PLB-2001", "800.00", "Water heater");
  await enter("PLB-2002", "650.00", "Shower valve");
  const bill = (reference: string) => page.getByRole("listitem", { name: `Demo Plumbing Co ${reference}` });
  await expect(bill("PLB-2001").getByTestId("status")).toHaveText("needs approval");
  await expect(bill("PLB-2001")).toContainText("Emailed the owner to approve or decline it in their portal.");
  const outbox = page.getByRole("list", { name: "Emails to owners" });
  await expect(outbox.getByRole("listitem")).toHaveCount(2);
  await expect(outbox).toContainText("Sent.");

  // The owner decides both in the portal.
  await owner.reload();
  const waiting = owner.getByRole("list", { name: "Waiting for you" });
  const first = waiting.getByRole("listitem").filter({ hasText: "PLB-2001" });
  await first.getByRole("button", { name: "Decline…" }).click();
  await first.getByLabel("Why you decline it").fill("Get a second quote");
  await first.getByRole("button", { name: "Decline", exact: true }).click();
  await expect(owner.getByText("Declined Demo Plumbing Co's PLB-2001.")).toBeVisible();
  await waiting.getByRole("listitem").filter({ hasText: "PLB-2002" }).getByRole("button", { name: "Approve" }).click();
  await expect(owner.getByText("Approved Demo Plumbing Co's PLB-2002.")).toBeVisible();
  const decided = owner.getByRole("list", { name: "Decided" });
  await expect(decided.getByRole("listitem").filter({ hasText: "PLB-2001" })).toContainText("declined");
  await expect(decided.getByRole("listitem").filter({ hasText: "PLB-2002" })).toContainText("approved");
  await expect(owner.getByText("No bill waits for your approval.")).toBeVisible();

  // Staff see both decisions: the declined bill is closed, the approved one is paid.
  await page.reload();
  await expect(bill("PLB-2001")).toHaveCount(0); // nothing left to pay on it
  await expect(bill("PLB-2002").getByTestId("status")).toHaveText("to set aside");
  // Birch Flats holds only its $250.00 reserve: setting $650.00 aside is refused, in words.
  await bill("PLB-2002").getByRole("button", { name: "Set aside" }).click();
  await expect(bill("PLB-2002").getByRole("alert")).toHaveText(
    "not enough money: the owner's account would be $400.00 short",
  );
  // The owner sends the money; then it is set aside and paid.
  await section(page, "Owners");
  const birch = page.getByRole("listitem", { name: "Demo Owner A, Birch Flats" });
  await birch.getByText("Record for Demo Owner A, Birch Flats").click();
  await birch.getByLabel("Contribution received", { exact: true }).fill("650.00");
  await birch.getByLabel("For", { exact: true }).fill("Shower valve");
  await birch.getByRole("button", { name: "Record contribution" }).click();
  await expect(birch).toContainText("$650.00 from Demo Owner A recorded.");
  // It covers the approved bill, so nothing more is available to draw.
  await expect(birch.getByTestId("available")).toHaveText("$0.00 available");
  await section(page, "Bills");
  await bill("PLB-2002").getByRole("button", { name: "Set aside" }).click();
  await expect(bill("PLB-2002").getByTestId("status")).toHaveText("set aside");
  await bill("PLB-2002").getByRole("button", { name: "Pay" }).click();
  await expect(page.getByText("PLB-2002 is paid.")).toBeVisible();
  await expect(bill("PLB-2002")).toHaveCount(0);
  await page.getByRole("combobox", { name: "Show", exact: true }).selectOption("all");
  await expect(bill("PLB-2002").getByTestId("status")).toHaveText("paid");
  await expect(bill("PLB-2001").getByTestId("status")).toHaveText("declined");
  await expect(bill("PLB-2001")).toContainText("Get a second quote");
});

test("staff add a recurring bill, enter this month's once and stop it, and record the decline of an owner with no login", async ({
  page,
}) => {
  const people = seed.features["recurring-bills"]!;
  await joinAsStaff(page, people.bookkeeper);
  await section(page, "Bills");
  await expect(page.getByText("No recurring bills yet.")).toBeVisible();
  await page.getByText("Add a recurring bill").click();
  await page.getByRole("combobox", { name: "Recurring vendor" }).selectOption({ label: "Demo Roofing Co" });
  await page
    .getByRole("combobox", { name: "For owner and property" })
    .selectOption({ label: "Demo Owner B, Aspen Row" });
  await page.getByLabel("Reference prefix").fill("GUTTER");
  await page.getByLabel("Monthly amount").fill("120.00");
  await page.getByLabel("For", { exact: true }).fill("Gutter cleaning");
  await page.getByLabel("Day of the month").fill("1");
  await page.getByLabel("First day").fill(firstOfThisMonth());
  await page.getByRole("button", { name: "Add recurring bill" }).click();
  await expect(page.getByText("GUTTER", { exact: false }).first()).toBeVisible();

  // This month's bill is entered once, under the prefix and the month.
  await page.getByLabel("Enter bills due through").fill(today());
  await page.getByRole("button", { name: "Enter recurring bills" }).click();
  await expect(page.getByText("1 bill entered.")).toBeVisible();
  await page.getByRole("button", { name: "Enter recurring bills" }).click();
  await expect(page.getByText("0 bills entered.")).toBeVisible();
  const month = today().slice(0, 7);
  const entered = page.getByRole("listitem", { name: `Demo Roofing Co GUTTER-${month}` });
  // Under Demo Owner B's $300.00 limit: no approval needed.
  await expect(entered.getByTestId("status")).toHaveText("to set aside");

  await page.getByRole("button", { name: "Stop", exact: true }).click();
  await page.getByLabel("Last day, GUTTER").fill(today());
  await page.getByRole("button", { name: "Stop it" }).click();
  await expect(page.getByText(`GUTTER stops after ${today()}.`)).toBeVisible();
  await expect(page.getByRole("button", { name: "Stop", exact: true })).toHaveCount(0);

  // A bill over the limit of an owner with no portal login: no email can go, so staff record
  // the owner's answer themselves, here a decline.
  await page.getByRole("combobox", { name: "Vendor", exact: true }).selectOption({ label: "Demo Roofing Co" });
  await page
    .getByRole("combobox", { name: "Owner and property", exact: true })
    .selectOption({ label: "Demo Owner A, Elm Court" });
  await page.getByLabel("Vendor's reference").fill("RF-3001");
  await page.getByLabel("Amount", { exact: true }).fill("900.00");
  await page.getByLabel("What for", { exact: true }).fill("Skylight");
  await page.getByRole("button", { name: "Enter bill" }).click();
  const skylight = page.getByRole("listitem", { name: "Demo Roofing Co RF-3001" });
  await expect(skylight).toContainText(
    "No email went to the owner: they have no active portal login. Ask them yourself.",
  );
  // It is due today, so Today lists it with the gutter bill.
  await section(page, "Today");
  const due = page.getByRole("region", { name: "Bills due this week" });
  await expect(due).toContainText("RF-3001");
  await expect(due).toContainText(`GUTTER-${month}`);
  await due.getByRole("link", { name: "Open Bills" }).click();
  await skylight.getByRole("button", { name: "Record a decline instead" }).click();
  await skylight.getByLabel("Declined by").fill("Demo Owner A, by phone");
  await skylight.getByLabel("Why").fill("Not this year");
  await skylight.getByRole("button", { name: "Record decline" }).click();
  await expect(page.getByText("The owner's decline of RF-3001 is recorded.")).toBeVisible();
  await expect(skylight).toHaveCount(0);
  await page.getByRole("combobox", { name: "Show", exact: true }).selectOption("all");
  await expect(skylight.getByTestId("status")).toHaveText("declined");
  await expect(skylight).toContainText("Declined by Demo Owner A, by phone: Not this year.");
});

test("staff take the month's management fees once, find no leasing fee due, and pay owners only what is available", async ({
  page,
}) => {
  const people = seed.features.owners!;
  await joinAsStaff(page, people.manager);
  await section(page, "Owners");
  const owner = (name: string) => page.getByRole("listitem", { name });
  await expect(owner("Demo Owner A, Birch Flats")).toContainText("$250.00 held, less a $250.00 reserve");

  // Nothing was collected yet this month: Demo Owner A's agreements take their $50.00 minimum.
  const fees = part(page, "Management fees");
  const nextMonth = new Date(Date.UTC(Number(today().slice(0, 4)), Number(today().slice(5, 7)), 1))
    .toISOString()
    .slice(0, 10);
  await fees.getByLabel("Period from").fill(firstOfThisMonth());
  await fees.getByLabel("Up to (not including)").fill(nextMonth);
  await fees.getByLabel("Taken on").fill(today());
  await fees.getByRole("button", { name: "Take fees" }).click();
  const taken = page.getByRole("list", { name: "Fees taken" });
  await expect(taken).toContainText("Demo Owner A, Birch Flats: $50.00 taken");
  await expect(owner("Demo Owner A, Birch Flats")).toContainText("$200.00 held");
  // Once per period: the same run again takes nothing more.
  await fees.getByRole("button", { name: "Take fees" }).click();
  await expect(taken).toContainText("Demo Owner A, Birch Flats: $50.00 taken");
  await expect(owner("Demo Owner A, Birch Flats")).toContainText("$200.00 held");

  // The demo's agreements take no leasing fee, and the page says so.
  const leasing = part(page, "Leasing fee");
  // The demo's leases began on the first of the month eleven months ago.
  await leasing
    .getByRole("combobox", { name: "Lease" })
    .selectOption({ label: `Aspen Row, Unit 1 from ${firstOfMonthsAgo(11)}` });
  await leasing.getByRole("button", { name: "Take leasing fee" }).click();
  await expect(page.getByText("No leasing fee: the owner's agreement in force takes none, so none is due for this lease.")).toBeVisible();

  // Aspen Row holds only its reserve: a draw is refused, in words.
  const aspen = owner("Demo Owner B, Aspen Row");
  await aspen.getByText("Record for Demo Owner B, Aspen Row").click();
  await aspen.getByRole("button", { name: "Record draw" }).click();
  await expect(aspen.getByRole("alert")).toHaveText(
    "nothing is available to draw: what the account holds is kept for the $200.00 reserve",
  );
  // The owner sends money in; a draw then pays out part of it, and another the rest.
  await aspen.getByLabel("Contribution received", { exact: true }).fill("300.00");
  await aspen.getByLabel("For", { exact: true }).fill("Roof fund");
  await aspen.getByRole("button", { name: "Record contribution" }).click();
  await expect(aspen).toContainText("$300.00 from Demo Owner B recorded.");
  await expect(aspen.getByTestId("available")).toHaveText("$300.00 available");
  await aspen.getByLabel("Draw amount (leave empty for all available)").fill("100.00");
  await aspen.getByRole("button", { name: "Record draw" }).click();
  await expect(aspen).toContainText("$100.00 paid out to Demo Owner B.");
  await expect(aspen.getByTestId("available")).toHaveText("$200.00 available");
  await aspen.getByRole("button", { name: "Record draw" }).click();
  await expect(aspen).toContainText("$200.00 paid out to Demo Owner B.");
  await expect(aspen.getByTestId("available")).toHaveText("$0.00 available");
});

test("only staff holding a role do its work: an admin grants pay-out and adds staff, staff sign in again with their code, and a disabled login is signed out for good", async ({
  browser,
  page,
}) => {
  const people = seed.features.roles!;
  const secret = await joinAsStaff(page, people.bookkeeper);
  await section(page, "Bills");
  await page.getByRole("combobox", { name: "Vendor", exact: true }).selectOption({ label: "Demo Plumbing Co" });
  await page
    .getByRole("combobox", { name: "Owner and property", exact: true })
    .selectOption({ label: "Demo Owner B, Aspen Row" });
  await page.getByLabel("Vendor's reference").fill("PLB-3001");
  await page.getByLabel("Amount", { exact: true }).fill("100.00");
  await page.getByLabel("What for").fill("Faucet washer");
  await page.getByRole("button", { name: "Enter bill" }).click();
  const bill = page.getByRole("listitem", { name: "Demo Plumbing Co PLB-3001" });
  await bill.getByRole("button", { name: "Set aside" }).click();
  await expect(bill.getByTestId("status")).toHaveText("set aside");
  // The bookkeeper holds no role: paying out is refused.
  await bill.getByRole("button", { name: "Pay" }).click();
  await expect(bill.getByRole("alert")).toHaveText("Demo Bookkeeper may not pay out money (the disburse role)");

  // The manager, an admin, grants it; and can't leave the company without an admin.
  const admin = await (await browser.newContext()).newPage();
  await joinAsStaff(admin, people.manager);
  await section(admin, "Staff");
  // A role box shows what the server holds: it turns once the change is recorded.
  await admin.getByRole("checkbox", { name: "Demo Bookkeeper: Pay out" }).click();
  await expect(admin.getByText("Demo Bookkeeper may now: Pay out.")).toBeVisible();
  await expect(admin.getByRole("checkbox", { name: "Demo Bookkeeper: Pay out" })).toBeChecked();
  await admin.getByRole("checkbox", { name: "Demo Manager: Admin" }).click();
  await expect(admin.getByRole("alert")).toHaveText(
    "the company keeps an admin: give another staff member admin first",
  );
  await expect(admin.getByRole("checkbox", { name: "Demo Manager: Admin" })).toBeChecked();
  await admin.getByLabel("Name").fill("Demo Leasing Agent");
  await admin.getByRole("button", { name: "Add" }).click();
  await expect(admin.getByText("Added Demo Leasing Agent; invite them from the Overview.")).toBeVisible();
  await expect(admin.getByRole("checkbox", { name: "Demo Leasing Agent: Approve" })).not.toBeChecked();

  // Now the bookkeeper pays it.
  await page.reload();
  await page.getByRole("listitem", { name: "Demo Plumbing Co PLB-3001" }).getByRole("button", { name: "Pay" }).click();
  await expect(page.getByText("PLB-3001 is paid.")).toBeVisible();
  await expect(page.getByRole("listitem", { name: "Demo Plumbing Co PLB-3001" })).toHaveCount(0);

  // Signed out, staff sign in again with their password and then their authenticator's code.
  await page.getByRole("button", { name: "Sign out" }).click();
  await expect(page.getByRole("heading", { name: "Sign in" })).toBeVisible();
  await page.getByLabel("Email").fill(people.emails.bookkeeper!);
  await page.getByLabel("Password").fill(PASSWORD);
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page.getByRole("alert")).toHaveText("enter the code from your authenticator app");
  // Each code is good once: the one that finished the invite was this window's, so the
  // authenticator's next one (accepted a window early, as for a clock running ahead).
  await page.getByLabel("Code from your authenticator app").fill(totp(secret, Date.now() + 30_000));
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page.getByRole("navigation", { name: "Sections" })).toBeVisible();
  await expect(page.getByRole("img", { name: "Demo Bookkeeper, Demo Property Management" })).toBeVisible();

  // The admin disables the bookkeeper's login, once asked: they are signed out for good.
  await section(admin, "Overview");
  const bookkeeper = admin.getByRole("row", { name: /^Demo Bookkeeper/ });
  await bookkeeper.getByRole("button", { name: "Disable", exact: true }).click();
  await bookkeeper.getByRole("button", { name: "Disable login" }).click();
  await expect(admin.getByText("Demo Bookkeeper's login is disabled; they are signed out.")).toBeVisible();
  await expect(bookkeeper.getByText("Disabled", { exact: true })).toBeVisible();
  await page.reload();
  await expect(page.getByRole("heading", { name: "Sign in" })).toBeVisible();
  await page.getByLabel("Email").fill(people.emails.bookkeeper!);
  await page.getByLabel("Password").fill(PASSWORD);
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page.getByRole("alert")).toHaveText("the email or password is wrong");
});

// A link's file downloads with the media type it says.
async function downloads(page: Page, name: string, type: string): Promise<void> {
  const href = (await page.getByRole("link", { name, exact: true }).first().getAttribute("href")) ?? "";
  const answer = await page.request.get(href);
  expect(answer.status(), `${name}: ${href}`).toBe(200);
  expect(answer.headers()["content-type"]).toContain(type);
  expect((await answer.body()).length).toBeGreaterThan(0);
}

const PDF = "application/pdf";
const CSV = "text/csv";
const XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet";

test("an owner reads last month's statement and their year-end package, and downloads each", async ({ page }) => {
  const people = seed.features["owner-portal"]!;
  await joinPortal(page, people.owner_a);
  await expect(page.getByRole("heading", { level: 1, name: "Your properties" })).toBeVisible();
  for (const name of ["Birch Flats", "Cedar House", "Elm Court"]) {
    await expect(page.getByRole("row", { name: new RegExp(`^${name}`) })).toContainText("$250.00");
  }

  // Last month: rent received, the management fee, the draw; each property ends at its reserve.
  const statement = part(page, "Statement");
  const birch = statement.getByRole("table", { name: "Birch Flats" });
  await expect(birch).toContainText("Received · Demo operating trust account");
  await expect(birch).toContainText("Owner draw · Demo operating trust account");
  await expect(birch).toContainText("· Demo Property Management");
  await expect(birch.getByRole("row", { name: /^Closing balance/ })).toContainText("$250.00");
  await expect(statement).not.toContainText("bank_cash");
  await downloads(page, "PDF", PDF);
  await downloads(page, "CSV", CSV);
  await downloads(page, "Excel", XLSX);

  // The year the demo's months fall in.
  const year = firstOfMonthsAgo(1).slice(0, 4);
  const yearEnd = part(page, "Year-end package");
  await yearEnd.getByLabel("Year").fill(year);
  await yearEnd.getByRole("button", { name: "Show" }).click();
  const birchYear = yearEnd.getByRole("table", { name: "Birch Flats" });
  await expect(birchYear.getByRole("row", { name: /^Collected from tenants/ })).not.toContainText("$0.00");
  await expect(birchYear.getByRole("row", { name: /^Closing balance/ })).toContainText("$250.00");
  await expect(yearEnd).toContainText("Rents for your 1099-MISC (box 1):");
  for (const [name, type] of [["PDF", PDF], ["CSV", CSV], ["Excel", XLSX], ["The year's statement", PDF]] as const) {
    const href = (await yearEnd.getByRole("link", { name, exact: true }).getAttribute("href")) ?? "";
    expect(href).toContain(year);
    const answer = await page.request.get(href);
    expect(answer.status(), `${name}: ${href}`).toBe(200);
    expect(answer.headers()["content-type"]).toContain(type);
  }
});

test("a tenant sees their lease, what comes each month and two months of charges and payments, and downloads them", async ({
  page,
}) => {
  const people = seed.features["tenant-portal"]!;
  await joinPortal(page, people.taylor);
  await expect(page.getByRole("heading", { level: 1, name: "Welcome, Taylor Demo" })).toBeVisible();
  await expect(page.getByText("Cedar House, House")).toBeVisible();
  await expect(page.getByText("Quinn Demo, Taylor Demo")).toBeVisible();
  // Last month's rent is paid and this month's not charged yet: nothing is due.
  await expect(page.getByTestId("balance-due")).toHaveText("$0.00");
  await expect(part(page, "Coming up")).toContainText("Rent: $2,250.00");

  const ledger = page.getByRole("table").filter({ hasText: "Charged" });
  await expect(ledger.getByRole("row", { name: /^\d{4}-\d{2}-\d{2} Rent due/ })).toHaveCount(2);
  await expect(ledger.getByRole("row", { name: /^\d{4}-\d{2}-\d{2} Payment/ })).toHaveCount(2);
  await expect(ledger.getByRole("row", { name: /^Balance at the end of/ })).toContainText(
    "$4,500.00$4,500.00$0.00",
  );
  await downloads(page, "PDF", PDF);
  await downloads(page, "CSV", CSV);
  await downloads(page, "Excel", XLSX);
});

test("a lease ended mid-month keeps only its days of the month's rent, and its deposit goes back less what it owes", async ({
  browser,
  page,
}) => {
  const people = seed.features["move-out"]!;
  await joinAsStaff(page, people.manager);
  await section(page, "Rent");
  await page.getByLabel("Rent due on").fill(firstOfThisMonth());
  await page.getByRole("button", { name: "Charge rent" }).click();
  await expect(page.getByText("6 rents and 1 recurring charge charged.")).toBeVisible();

  // Drew Sample's last day was yesterday: this month's $1,180.00 comes down to the days before.
  const yesterday = new Date(Date.now() - 86_400_000).toISOString().slice(0, 10);
  const [year, month] = today().split("-").map(Number) as [number, number];
  const daysInMonth = new Date(Date.UTC(year, month, 0)).getUTCDate();
  const daysInEffect = yesterday.slice(0, 7) === today().slice(0, 7) ? Number(yesterday.slice(8, 10)) : 0;
  const cents = Math.round((118_000 * daysInEffect) / daysInMonth);
  const dollars = (c: number) =>
    `$${Math.floor(c / 100).toLocaleString("en-US")}.${String(c % 100).padStart(2, "0")}`;

  await section(page, "Move-out");
  const lease = page.getByRole("region", { name: "Aspen Row / Unit 1: Drew Sample" });
  await expect(lease).toContainText("owes $1,180.00");
  await lease.getByText("Move-out for Unit 1").click();
  await lease.getByLabel("Last day").fill(yesterday);
  await lease.getByRole("button", { name: "End the lease" }).click();
  await expect(lease).toContainText(`The lease ends on ${yesterday}.`);
  await expect(lease.getByText("Ended, deposit to return")).toBeVisible();
  await expect(lease).toContainText(`owes ${dollars(cents)}`);

  // Cleaning is charged; what is owed comes out of the deposit, the rest goes back.
  await lease.getByLabel("Cleaning or damage").fill("Carpet cleaning");
  await lease.getByLabel("Amount").fill("100.00");
  await lease.getByRole("button", { name: "Charge it" }).click();
  await expect(lease).toContainText("Charged $100.00: Carpet cleaning.");
  const kept = cents + 10_000;
  const back = 118_000 - kept;
  await expect(lease).toContainText(`Keeps ${dollars(kept)} for what the lease owes; ${dollars(back)} goes back.`);
  await lease.getByRole("button", { name: "Return the deposit" }).click();
  await expect(lease).toContainText(`Returned ${dollars(back)} to the tenants; the rest paid what the lease owed.`);
  await expect(lease.getByText("Deposit returned")).toBeVisible();
  await downloads(page, "Statement for the tenants (PDF)", PDF);

  // The tenant downloads the same statement from the portal.
  const tenant = await (await browser.newContext()).newPage();
  await joinPortal(tenant, people.drew);
  await downloads(tenant, "Deposit statement (PDF)", PDF);
});

test("staff list a vacant unit on the public page; someone applies from it, staff approve, take it off the page and close the link", async ({
  browser,
  page,
}) => {
  const people = seed.features.listings!;
  await joinAsStaff(page, people.manager);
  await page.getByRole("combobox", { name: "Unit", exact: true }).selectOption({ label: "Aspen Row, Unit 2" });
  await page.getByLabel("Rent a month").fill("1240.00");
  await page.getByRole("button", { name: "Open a link" }).click();
  await expect(page.getByTestId("application-link")).toContainText("/apply#");
  const opened = new URL((await page.getByTestId("application-link").textContent()) ?? "");
  const link = page.getByRole("listitem", { name: "Aspen Row, Unit 2" });
  await link.getByLabel("Description for the public page (optional)").fill("Two bedrooms, second floor.");
  await link.getByRole("button", { name: "List on the public page" }).click();
  await expect(link).toContainText("0 applications; on the public page");

  // Anyone, signed in or not, sees it on the PMC's vacancy page and applies from there.
  const visitor = await (await browser.newContext()).newPage();
  await visitor.goto(`/listings?pmc=${people.pmc_id}`);
  const listing = visitor.getByRole("listitem", { name: "Aspen Row, Unit 2" });
  await expect(listing).toContainText("$1,240.00 a month");
  await expect(listing).toContainText("Two bedrooms, second floor.");
  await listing.getByRole("link", { name: "Apply" }).click();
  await expect(visitor.getByRole("heading", { name: "Apply for Aspen Row, Unit 2" })).toBeVisible();
  await visitor.getByLabel("Move-in day").fill(new Date(Date.now() + 20 * 86_400_000).toISOString().slice(0, 10));
  await visitor.getByLabel("Monthly income of everyone applying, together").fill("5200");
  await visitor.getByLabel("Full name").fill("Applicant Listing");
  await visitor.getByLabel("Email").fill("applicant.listing@example.com");
  await visitor.getByRole("button", { name: "Send application" }).click();
  const statusUrl = new URL((await visitor.getByTestId("status-url").textContent()) ?? "");

  // It waits for a decision on Today and the overview; staff screen and approve it.
  await section(page, "Today");
  await expect(page.getByRole("region", { name: "Applications waiting" })).toContainText("Applicant Listing");
  await section(page, "Overview");
  const application = page.getByRole("listitem", { name: "Application from Applicant Listing" });
  await application.getByRole("button", { name: "Screen" }).click();
  await expect(application).toContainText("Fake Screening Partner: Recommends approval");
  await application.getByLabel("Decision").selectOption({ label: "Approve" });
  await application.getByRole("button", { name: "Record decision" }).click();
  await expect(application).toHaveCount(0);
  await visitor.goto(statusUrl.pathname + statusUrl.hash);
  await expect(visitor.getByTestId("application-status")).toContainText("Approved");

  // Taken off the page, it is gone from it.
  await page.getByRole("listitem", { name: "Aspen Row, Unit 2" }).getByRole("button", { name: "Take off the page" }).click();
  await expect(
    page.getByRole("listitem", { name: "Aspen Row, Unit 2" }).getByRole("button", { name: "List on the public page" }),
  ).toBeVisible();
  await visitor.goto(`/listings?pmc=${people.pmc_id}`);
  await expect(visitor.getByText("Nothing is available right now.")).toBeVisible();

  // Closed, the link takes no more applications.
  await page.getByRole("listitem", { name: "Aspen Row, Unit 2" }).getByRole("button", { name: "Close" }).click();
  await expect(page.getByRole("listitem", { name: "Aspen Row, Unit 2" })).toHaveCount(0);
  await visitor.goto(opened.pathname + opened.hash);
  await expect(visitor.getByRole("alert")).toHaveText("this application link is closed or doesn't exist");
});

test("staff set management terms with a leasing fee, late fees, a bank account and a vendor, and a new lease takes its leasing fee", async ({
  page,
}) => {
  const people = seed.features.setup!;
  await joinAsStaff(page, people.manager);
  await section(page, "Setup");

  await page.getByRole("combobox", { name: "Kind" }).selectOption({ label: "Operating" });
  await page.getByLabel("Bank account name").fill("Second operating account");
  await page.getByRole("button", { name: "Add bank account" }).click();
  await expect(part(page, "Trust bank accounts")).toContainText("Second operating account (Operating)");
  const vendors = page.getByRole("region", { name: "Vendors" });
  await vendors.getByLabel("Vendor's name").fill("Demo Electric Co");
  await vendors.getByRole("button", { name: "Add" }).click();
  await expect(vendors.getByRole("listitem").filter({ hasText: "Demo Electric Co" })).toBeVisible();

  // New terms for Aspen Row from today: a 50% leasing fee on leases starting from now.
  const terms = part(page, "Management terms");
  await terms.getByRole("combobox", { name: "For" }).selectOption({ label: "Demo Owner B, Aspen Row" });
  await terms.getByLabel("In force from").fill(today());
  await terms.getByLabel("Fee, percent of rent and recurring fees collected").fill("10");
  await terms.getByLabel("Leasing fee, percent of rent").fill("50");
  await terms.getByLabel("Reserve").fill("200.00");
  await terms.getByLabel("Owner approves bills over").fill("300.00");
  await terms.getByRole("button", { name: "Set terms" }).click();
  await expect(terms.getByRole("listitem").filter({ hasText: "Demo Owner B, Aspen Row" })).toContainText(`from ${today()}`);

  // Late fees for Aspen Row: three days' grace, then $25.00.
  const aspen = page.getByRole("listitem", { name: "Aspen Row" });
  await aspen.getByText("Add to Aspen Row").click();
  await aspen.getByLabel("Late fees from").fill(today());
  await aspen.getByLabel("Grace days").fill("3");
  await aspen.getByLabel("Flat fee").fill("25.00");
  await aspen.getByRole("button", { name: "Set late fees" }).click();
  await expect(aspen).toContainText("After 3 days' grace: $25.00");

  // A new tenant on the vacant unit, from today.
  await page.getByRole("combobox", { name: "Tenant of" }).selectOption({ label: "Aspen Row" });
  await page.getByLabel("Tenant's name").fill("Avery Sample");
  await page.getByRole("button", { name: "Add tenant" }).click();
  await page.getByRole("combobox", { name: "Unit", exact: true }).selectOption({ label: "Aspen Row, Unit 2" });
  await page.getByLabel("Starts").fill(today());
  await page.getByLabel("Rent a month").fill("1240.00");
  await page.getByLabel("Avery Sample").check();
  await page.getByRole("button", { name: "Add lease" }).click();
  await expect(aspen).toContainText(`Unit 2: Avery Sample: from ${today()}, month to month, $1,240.00 a month`);

  // Its leasing fee is half a month's rent, once the owner has the money for it.
  await section(page, "Owners");
  const owner = page.getByRole("listitem", { name: "Demo Owner B, Aspen Row" });
  await owner.getByText("Record for Demo Owner B, Aspen Row").click();
  await owner.getByLabel("Contribution received", { exact: true }).fill("620.00");
  await owner.getByRole("button", { name: "Record contribution" }).click();
  await expect(owner).toContainText("$620.00 from Demo Owner B recorded.");
  const leasing = part(page, "Leasing fee");
  await leasing.getByRole("combobox", { name: "Lease" }).selectOption({ label: `Aspen Row, Unit 2 from ${today()}` });
  await leasing.getByRole("button", { name: "Take leasing fee" }).click();
  await expect(page.getByText("A leasing fee of $620.00 is taken.")).toBeVisible();
  await expect(owner).toContainText("$200.00 held");

  // A second owner of Aspen Row, held in the new operating account.
  await section(page, "Setup");
  await aspen.getByText("Add to Aspen Row").click();
  await aspen.getByRole("combobox", { name: "Owner", exact: true }).selectOption({ label: "Demo Owner A" });
  await aspen.getByRole("combobox", { name: "Held in" }).selectOption({ label: "Second operating account" });
  await aspen.getByRole("button", { name: "Add owner" }).click();
  await expect(aspen.getByText(/^Owned by/)).toContainText("Demo Owner A");
  await expect(
    terms.getByRole("combobox", { name: "For" }).getByRole("option", { name: "Demo Owner A, Aspen Row" }),
  ).toHaveCount(1);
});

test("staff download every report the overview offers, and every staff report in each format", async ({ page }) => {
  const people = seed.features.downloads!;
  await joinAsStaff(page, people.manager);
  await section(page, "Overview");
  await downloads(page, "Rent roll (PDF)", PDF);
  await downloads(page, "Trial balance (Excel)", XLSX);
  await downloads(page, "Balance sheet (PDF)", PDF);
  await downloads(page, "Cash flow, last month (Excel)", XLSX);
  await downloads(page, "Vacancy (PDF)", PDF);
  await downloads(page, "Work orders (Excel)", XLSX);
  await downloads(page, "Leases ending in 90 days (Excel)", XLSX);
  await downloads(page, "Tenant directory (Excel)", XLSX);
  await downloads(page, "Owner directory (Excel)", XLSX);
  await downloads(page, "Vendor directory (Excel)", XLSX);
  await downloads(page, "Whole ledger (CSV, zip)", "application/zip");

  // The reports the API exports for staff (the AI reads the same), each as CSV, Excel and PDF.
  const day = today();
  const reports: [string, string[]][] = [
    ["rent_roll", [day]],
    ["trial_balance", [day]],
    ["lease_expirations", [day]],
    ["delinquency", [day]],
    ["owner_balances", [day]],
    ["security_deposits", [day]],
    ["unpaid_bills", [day]],
    ["general_ledger", [firstOfMonthsAgo(2), day]],
    ["audit_log", [firstOfMonthsAgo(2), day]],
    ["vendor_payments", [day.slice(0, 4)]],
    ["balance_sheet", [day]],
    ["cash_flow", [firstOfMonthsAgo(2), day]],
    ["vacancy", [day]],
    ["work_orders", [day]],
    ["tenant_directory", [day]],
    ["owner_directory", [day]],
    ["vendor_directory", [day]],
  ];
  for (const [report, args] of reports) {
    for (const [format, type] of [["csv", CSV], ["xlsx", XLSX], ["pdf", PDF]] as const) {
      const query = new URLSearchParams([...args.map((a) => ["arg", a]), ["format", format]]);
      const answer = await page.request.get(`/api/reports/trust_report_${report}?${query}`);
      expect(answer.status(), `${report} as ${format}`).toBe(200);
      expect(answer.headers()["content-type"], `${report} as ${format}`).toContain(type);
    }
  }
});

test("staff keep a vendor's W-9, a bill's receipt and a move-out photo, refuse a file that isn't what it says, and remove one", async ({
  page,
}) => {
  const people = seed.features.documents!;
  await joinAsStaff(page, people.bookkeeper);
  await section(page, "Documents");
  const keep = async (kind: string, about: string, aboutLabel: string, file: { name: string; mimeType: string; buffer: Buffer }) => {
    await page.getByRole("combobox", { name: "Kind" }).selectOption({ label: kind });
    // A bill or a lease is named with its day; pick it by what it starts with.
    const select = page.getByRole("combobox", { name: about });
    const value = await select.locator("option").filter({ hasText: aboutLabel }).first().getAttribute("value");
    await select.selectOption(value ?? "");
    await page.getByLabel("File").setInputFiles(file);
    await page.getByRole("button", { name: "Keep it" }).click();
  };
  const pdf = (text: string) => Buffer.from(`%PDF-1.4\n% ${text}\n`);
  const png = Buffer.from("89504e470d0a1a0a0000000d49484452", "hex");
  await keep("W-9", "Vendor", "Demo Plumbing Co", { name: "w9-plumbing.pdf", mimeType: "application/pdf", buffer: pdf("synthetic W-9") });
  await expect(page.getByText("Kept w9-plumbing.")).toBeVisible();
  await keep("Receipt", "Bill", "Demo Roofing Co RF-2207", { name: "roof-receipt.pdf", mimeType: "application/pdf", buffer: pdf("synthetic receipt") });
  await expect(page.getByText("Kept roof-receipt.")).toBeVisible();
  await keep("Move-out photo", "Lease", "Aspen Row / Unit 1: Drew Sample", { name: "kitchen.png", mimeType: "image/png", buffer: png });
  await expect(page.getByText("Kept kitchen.")).toBeVisible();
  const kept = page.getByRole("list", { name: "Documents" }).getByRole("listitem");
  await expect(kept).toHaveCount(3);

  // A file whose bytes aren't the type its name says is refused, and nothing is kept.
  await page.getByRole("combobox", { name: "Kind" }).selectOption({ label: "W-9" });
  await page.getByLabel("File").setInputFiles({ name: "not-a.pdf", mimeType: "application/pdf", buffer: Buffer.from("plain text") });
  await page.getByRole("button", { name: "Keep it" }).click();
  await expect(page.getByRole("alert")).toHaveText("the file isn't a PDF");
  await expect(kept).toHaveCount(3);

  // Removed, it leaves the list (a removal is kept as a row; nothing is deleted).
  await page.getByRole("button", { name: "Remove kitchen" }).click();
  await expect(page.getByText("Removed kitchen.")).toBeVisible();
  await expect(kept).toHaveCount(2);
  const file = await page.request.get((await page.getByRole("link", { name: "w9-plumbing" }).getAttribute("href")) ?? "");
  expect(file.status()).toBe(200);
  expect(Buffer.from(await file.body()).equals(pdf("synthetic W-9"))).toBe(true);
});

test("staff carry over what the old system paid each vendor this year, and the year's vendor payments count it", async ({
  page,
}) => {
  const people = seed.features.switching!;
  await joinAsStaff(page, people.manager);
  await section(page, "Switching");
  const carry = part(page, "Vendor payments before the cutover");
  await carry.getByLabel("Cutover day").fill(firstOfMonthsAgo(2));
  await carry.getByLabel("Vendor payments (CSV)").setInputFiles({
    name: "vendor-payments.csv",
    mimeType: "text/csv",
    buffer: Buffer.from("Vendor,Paid\nDemo Plumbing Co,1200.00\nDemo Roofing Co,800.50\n"),
  });
  await carry.getByRole("button", { name: "Carry over" }).click();
  await expect(carry).toContainText("2 added.");
  // Again: once per vendor and year, so nothing more.
  await carry.getByRole("button", { name: "Carry over" }).click();
  await expect(carry).toContainText("0 added; 2 already here.");

  const year = firstOfMonthsAgo(2).slice(0, 4);
  const csv = await page.request.get(`/api/reports/trust_report_vendor_payments?arg=${year}&format=csv`);
  expect(csv.status()).toBe(200);
  const text = await csv.text();
  expect(text).toContain("Demo Plumbing Co");
  expect(text).toContain("1200.00");
  expect(text).toContain("800.50");
});

test("staff put bills in expense accounts on Schedule E lines, and the owner reads the year's income statement", async ({
  browser,
  page,
}) => {
  const people = seed.features.expenses!;
  await joinAsStaff(page, people.manager);

  // The demo's expense accounts, and one more.
  await section(page, "Setup");
  const accounts = part(page, "Expense accounts");
  await expect(accounts).toContainText("Repairs (Schedule E line 14: Repairs)");
  await accounts.getByLabel("Expense account").fill("Insurance");
  await accounts.getByRole("combobox", { name: "Schedule E line" }).selectOption({ label: "9: Insurance" });
  await accounts.getByRole("button", { name: "Add expense account" }).click();
  await expect(accounts).toContainText("Insurance (Schedule E line 9: Insurance)");

  // A water bill entered in Water; a hose in none, then put in Water; both set aside.
  await section(page, "Bills");
  const enter = async (reference: string, amount: string, memo: string, account?: string) => {
    await page.getByRole("combobox", { name: "Vendor", exact: true }).selectOption({ label: "Demo Plumbing Co" });
    await page
      .getByRole("combobox", { name: "Owner and property", exact: true })
      .selectOption({ label: "Demo Owner B, Aspen Row" });
    await page.getByLabel("Vendor's reference").fill(reference);
    await page.getByLabel("Amount", { exact: true }).fill(amount);
    await page.getByLabel("What for", { exact: true }).fill(memo);
    if (account) {
      await page
        .getByRole("combobox", { name: "Expense account", exact: true })
        .selectOption({ label: account });
    }
    await page.getByRole("button", { name: "Enter bill" }).click();
    await expect(page.getByText(`Bill ${reference} entered.`)).toBeVisible();
  };
  await enter("WTR-1", "45.00", "Water, last month", "Water (Schedule E line 17: Utilities)");
  await enter("HOSE-1", "12.00", "Garden hose");
  const bill = (reference: string) => page.getByRole("listitem", { name: `Demo Plumbing Co ${reference}` });
  await expect(bill("WTR-1").getByTestId("expense-account")).toHaveText("In Water.");
  await expect(bill("HOSE-1").getByTestId("expense-account")).toHaveText("In no expense account yet.");
  await bill("HOSE-1").getByRole("combobox", { name: "Expense account, HOSE-1" }).selectOption({ label: "Water" });
  await bill("HOSE-1").getByRole("button", { name: "Put in" }).click();
  await expect(page.getByText("HOSE-1 is in Water.")).toBeVisible();
  await expect(bill("HOSE-1").getByTestId("expense-account")).toHaveText("In Water.");
  for (const reference of ["WTR-1", "HOSE-1"]) {
    await bill(reference).getByRole("button", { name: "Set aside" }).click();
    await expect(bill(reference).getByTestId("status")).toHaveText("set aside");
  }

  // A lawn service every month, in Landscaping, then moved to Repairs.
  await page.getByText("Add a recurring bill").click();
  await page.getByRole("combobox", { name: "Recurring vendor" }).selectOption({ label: "Demo Roofing Co" });
  await page
    .getByRole("combobox", { name: "For owner and property" })
    .selectOption({ label: "Demo Owner B, Aspen Row" });
  await page.getByLabel("Reference prefix").fill("LAWN");
  await page.getByLabel("Monthly amount").fill("40.00");
  await page.getByLabel("For", { exact: true }).fill("Lawn service");
  await page
    .getByRole("combobox", { name: "Recurring expense account" })
    .selectOption({ label: "Landscaping (Schedule E line 7: Cleaning and maintenance)" });
  await page.getByRole("button", { name: "Add recurring bill" }).click();
  const lawn = page.getByRole("listitem", { name: "Demo Roofing Co LAWN" });
  await expect(lawn.getByTestId("expense-account")).toHaveText("In Landscaping.");
  await lawn.getByRole("combobox", { name: "Expense account, LAWN" }).selectOption({ label: "Repairs" });
  await lawn.getByRole("button", { name: "Move" }).click();
  await expect(page.getByText("LAWN is entered in Repairs from now on.")).toBeVisible();

  // Staff can download any owner's income statement from the Owners page.
  await section(page, "Owners");
  const statement = page
    .getByRole("listitem", { name: "Demo Owner B, Aspen Row" })
    .getByRole("link", { name: "Demo Owner B's income statement this year (PDF)" });
  const pdf = await page.request.get((await statement.getAttribute("href")) ?? "");
  expect(pdf.status()).toBe(200);
  expect((await pdf.body()).subarray(0, 4).toString()).toBe("%PDF");

  // The owner reads this year's income statement: the water and hose are Utilities.
  const owner = await (await browser.newContext()).newPage();
  await joinPortal(owner, people.owner_b);
  const card = owner.locator("section", { has: owner.getByRole("heading", { level: 2, name: "Income statement" }) });
  await card.getByLabel("Year").fill(today().slice(0, 4));
  await card.getByRole("button", { name: "Show" }).click();
  const aspen = card.getByRole("table", { name: "Aspen Row" });
  await expect(aspen.getByRole("row", { name: "Water $57.00" })).toBeVisible();
  await expect(card.getByRole("list", { name: "Schedule E, Aspen Row" })).toContainText(
    "Schedule E line 17: Utilities$57.00",
  );
  const csv = await owner.request.get((await card.getByRole("link", { name: "CSV" }).getAttribute("href")) ?? "");
  expect(await csv.text()).toContain("expense: Water");
});

test("a bookkeeper proposes journal entries; the manager posts one, the ledger refuses one that is too much, and the manager declines it", async ({
  browser,
  page,
}) => {
  const people = seed.features.journal!;
  const OPERATING = "Demo operating trust account";
  await joinAsStaff(page, people.bookkeeper);
  await section(page, "Journal");
  await expect(page.getByTestId("empty-state")).toContainText("No journal entries yet");

  async function propose(memo: string, amount: string) {
    const form = part(page, "Propose an entry");
    await form.getByLabel("What it corrects").fill(memo);
    await form.getByLabel("Line 1 from").selectOption({ label: `${OPERATING}: Demo Owner B / Aspen Row` });
    await form.getByLabel("Line 1 to").selectOption({ label: `${OPERATING}: Demo Owner A / Birch Flats` });
    await form.getByLabel("Line 1 amount").fill(amount);
    await form.getByRole("button", { name: "Propose entry" }).click();
    await expect(
      form.getByText(`Proposed: ${memo}. Another staff member who may approve posts it.`),
    ).toBeVisible();
  }
  await propose("Birch Flats rent deposited as Aspen Row", "10.00");
  await propose("Typed an extra zero", "1000000.00");
  const entry = (p: Page, memo: string) => p.getByRole("listitem", { name: `${today()} ${memo}` });
  const moved = entry(page, "Birch Flats rent deposited as Aspen Row");
  await expect(moved.getByTestId("status")).toHaveText("waiting");
  // Whoever proposed it doesn't post it.
  await expect(moved.getByRole("button", { name: "Post" })).toHaveCount(0);
  await expect(moved).toContainText("You proposed it: another staff member who may approve posts it.");

  const manager = await (await browser.newContext()).newPage();
  await joinAsStaff(manager, people.manager);
  await section(manager, "Journal");
  const toPost = entry(manager, "Birch Flats rent deposited as Aspen Row");
  await expect(toPost.getByRole("row", { name: /Demo Owner B \/ Aspen Row .*Demo Owner A \/ Birch Flats \$10\.00/ })).toBeVisible();
  await toPost.getByRole("button", { name: "Post" }).click();
  await expect(manager.getByText("Posted: Birch Flats rent deposited as Aspen Row.")).toBeVisible();
  await expect(toPost.getByTestId("status")).toHaveText("posted");
  await expect(toPost).toContainText("Posted by Demo Manager.");

  const tooMuch = entry(manager, "Typed an extra zero");
  await tooMuch.getByRole("button", { name: "Post" }).click();
  await expect(tooMuch.getByRole("alert")).toHaveText(/^not enough money: the owner's account would be \$[\d,]+\.\d\d short$/);
  await expect(tooMuch.getByTestId("status")).toHaveText("waiting");
  await tooMuch.getByRole("button", { name: "Decline" }).click();
  await tooMuch.getByLabel("Why").fill("An extra zero");
  await tooMuch.getByRole("button", { name: "Decline entry" }).click();
  await expect(manager.getByText("Declined: Typed an extra zero.")).toBeVisible();
  await expect(tooMuch.getByTestId("status")).toHaveText("declined");

  // The bookkeeper sees what became of both.
  await page.reload();
  await expect(entry(page, "Birch Flats rent deposited as Aspen Row").getByTestId("status")).toHaveText("posted");
  await expect(entry(page, "Typed an extra zero")).toContainText("Declined by Demo Manager: An extra zero.");
});

test("staff budget an owner's property for the year, line by line, and download it against what happened", async ({ page }) => {
  const people = seed.features.budgets!;
  await joinAsStaff(page, people.manager);
  await section(page, "Budgets");
  const year = Number(today().slice(0, 4));
  await page.getByRole("combobox", { name: "Owner and property" }).selectOption({ label: "Demo Owner B, Aspen Row" });
  const rent = page.getByRole("row", { name: /^Income: rent/ });
  await expect(rent).toContainText("Not budgeted");

  await rent.getByLabel("Income: rent, a year").fill("24000");
  await rent.getByRole("button", { name: "Save income: rent" }).click();
  await expect(page.getByText(`Income: rent: 12 months changed in ${year}.`)).toBeVisible();
  await expect(rent).toContainText("$2,000.00 a month");

  const repairs = page.getByRole("row", { name: /^Expense: Repairs/ });
  await repairs.getByLabel("Expense: Repairs, a year").fill("1000");
  await repairs.getByRole("button", { name: "Save expense: repairs" }).click();
  await expect(page.getByText(`Expense: Repairs: 12 months changed in ${year}.`)).toBeVisible();
  await expect(repairs).toContainText("Varies by month");
  // Saving the same again changes nothing.
  await repairs.getByRole("button", { name: "Save expense: repairs" }).click();
  await expect(page.getByText(`Expense: Repairs: 0 months changed in ${year}.`)).toBeVisible();

  await downloads(page, `Demo Owner B's budget against actual, ${year} (Excel)`, XLSX);
  const href = (await page.getByRole("link", { name: /budget against actual/ }).getAttribute("href")) ?? "";
  const csv = await page.request.get(href.replace("format=xlsx", "format=csv"));
  expect(await csv.text()).toContain("Aspen Row,income: rent,24000.00,");
});

test("an admin makes a custom role and gives it: its holder changes only what it allows, and still sees everything", async ({
  browser,
  page,
}) => {
  const people = seed.features["custom-roles"]!;
  await joinAsStaff(page, people.manager);
  await section(page, "Staff");
  await page.getByLabel("New role").fill("Leasing agent");
  await page.getByRole("button", { name: "Make role" }).click();
  await expect(page.getByText("Made Leasing agent: choose what it may change, then who holds it.")).toBeVisible();
  const role = page.getByRole("listitem", { name: "Leasing agent" });
  await role.getByLabel("Leasing agent: leasing").click();
  await expect(page.getByText("Leasing agent may now change leasing.")).toBeVisible();
  await role.getByLabel("Leasing agent: documents").click();
  await expect(role).toContainText("Leasing agent may change: documents, leasing.");
  await expect(role.getByLabel("Leasing agent: documents")).toBeChecked();
  await role.getByLabel("Demo Bookkeeper holds Leasing agent").click();
  await expect(page.getByText("Demo Bookkeeper now holds Leasing agent.")).toBeVisible();

  // The bookkeeper sees the bills, and may not enter one.
  const bookkeeper = await (await browser.newContext()).newPage();
  await joinAsStaff(bookkeeper, people.bookkeeper);
  await section(bookkeeper, "Bills");
  await bookkeeper.getByRole("combobox", { name: "Vendor", exact: true }).selectOption({ label: "Demo Plumbing Co" });
  await bookkeeper.getByLabel("Vendor's reference").fill("PLB-4001");
  await bookkeeper.getByLabel("Amount", { exact: true }).fill("80.00");
  await bookkeeper.getByLabel("What for").fill("Drain");
  await bookkeeper.getByRole("button", { name: "Enter bill" }).click();
  await expect(bookkeeper.getByRole("alert")).toHaveText("your roles don't let you change bills: an admin can add it");

  // Once the role allows bills too, they may.
  await role.getByLabel("Leasing agent: bills").click();
  await expect(page.getByText("Leasing agent may now change bills.")).toBeVisible();
  await bookkeeper.getByRole("button", { name: "Enter bill" }).click();
  await expect(bookkeeper.getByText("Bill PLB-4001 entered.")).toBeVisible();
});

// A day some whole days from today (UTC).
const daysFromToday = (days: number) => new Date(Date.now() + days * 86_400_000).toISOString().slice(0, 10);

test("staff record a vendor's insurance, see it running out on Today, and record the renewal", async ({ page }) => {
  const people = seed.features.vendors!;
  await joinAsStaff(page, people.manager);
  await section(page, "Vendors");
  const plumbing = page.getByRole("listitem", { name: "Demo Plumbing Co" });
  await expect(plumbing).toContainText("No insurance on file.");

  // A policy that runs out in ten days, and one that ran out yesterday.
  const record = async (coverage: string, number: string, starts: string, expires: string) => {
    await page.getByRole("combobox", { name: "Vendor", exact: true }).selectOption({ label: "Demo Plumbing Co" });
    await page.getByRole("combobox", { name: "Coverage" }).selectOption(coverage);
    await page.getByLabel("Carrier").fill("Synthetic Mutual");
    await page.getByLabel("Policy number").fill(number);
    await page.getByLabel("Starts").fill(starts);
    await page.getByLabel("Expires").fill(expires);
    await page.getByRole("button", { name: "Record policy" }).click();
  };
  await record("general liability", "GL-0001", firstOfMonthsAgo(11), daysFromToday(10));
  await expect(page.getByText("Demo Plumbing Co's general liability policy GL-0001 is recorded.")).toBeVisible();
  await page.getByLabel("Limit (optional)").fill("1000000");
  await record("workers compensation", "WC-0001", firstOfMonthsAgo(23), daysFromToday(-1));
  await expect(page.getByText("Demo Plumbing Co's workers compensation policy WC-0001 is recorded.")).toBeVisible();
  const liability = page.getByRole("listitem", { name: "Demo Plumbing Co, general liability" });
  await expect(liability.getByTestId("standing")).toHaveText("expires in 10 days");
  const workers = page.getByRole("listitem", { name: "Demo Plumbing Co, workers compensation" });
  await expect(workers.getByTestId("standing")).toHaveText("expired");
  await expect(workers).toContainText("limit $1,000,000.00");

  // A policy can't expire before it starts.
  await record("other", "OT-0001", daysFromToday(5), daysFromToday(4));
  await expect(page.getByRole("alert")).toHaveText("check what you entered and try again");

  // Today lists both; the renewal takes the place of the policy running out.
  await section(page, "Today");
  await expect(page.getByText(`Demo Plumbing Co, general liability: expires in 10 days (${daysFromToday(10)})`)).toBeVisible();
  await expect(page.getByText(`Demo Plumbing Co, workers compensation: expired (${daysFromToday(-1)})`)).toBeVisible();
  await section(page, "Vendors");
  await record("general liability", "GL-0002", today(), daysFromToday(365));
  await expect(page.getByText("Demo Plumbing Co's general liability policy GL-0002 is recorded.")).toBeVisible();
  await expect(liability.getByTestId("standing")).toHaveText("current");
  await expect(liability).toContainText("GL-0002");
  await downloads(page, "Vendor insurance (PDF)", PDF);
  const href = (await page.getByRole("link", { name: "Vendor insurance (PDF)" }).getAttribute("href")) ?? "";
  const csv = await (await page.request.get(href.replace("format=pdf", "format=csv"))).text();
  expect(csv).toContain(`Demo Plumbing Co,`);
  expect(csv).toContain(`general liability,Synthetic Mutual,GL-0002,${today()},${daysFromToday(365)},,current`);
  expect(csv).toContain("no insurance on file,Demo Roofing Co,");
});

test("staff record a lease's move-in and move-out inspections room by room, and download them side by side", async ({ page }) => {
  const people = seed.features.inspections!;
  await joinAsStaff(page, people.manager);
  await section(page, "Move-out");
  const lease = page.getByRole("region", { name: "Aspen Row / Unit 1: Drew Sample" });
  await lease.getByRole("button", { name: "Inspections" }).click();
  const panel = page.getByRole("region", { name: "Inspections, Aspen Row / Unit 1: Drew Sample" });
  await expect(panel).toContainText("No inspection recorded yet.");

  // Move-in, the day the lease started: every room good, the keys noted.
  await panel.getByLabel("Inspected on").fill(firstOfMonthsAgo(11));
  await panel.getByLabel("Notes", { exact: true }).fill("Two keys given");
  await panel.getByRole("button", { name: "Record inspection" }).click();
  await expect(panel).toContainText(`The move-in inspection of ${firstOfMonthsAgo(11)} is recorded.`);
  await expect(panel).toContainText(`Move-in, ${firstOfMonthsAgo(11)}: 4 rooms`);

  // Move-out today: the kitchen damaged, and a hallway the move-in didn't list.
  await panel.getByRole("combobox", { name: "Inspection", exact: true }).selectOption("move-out");
  await panel.getByLabel("Inspected on").fill(today());
  await panel.getByRole("combobox", { name: "Room 1 condition" }).selectOption("damaged");
  await panel.getByLabel("Room 1 note").fill("Burn on the counter");
  await panel.getByRole("button", { name: "Add a room" }).click();
  await panel.getByLabel("Room 5", { exact: true }).fill("kitchen");
  await panel.getByRole("button", { name: "Record inspection" }).click();
  await expect(panel.getByRole("alert")).toHaveText("check what you entered and try again");
  await panel.getByLabel("Room 5", { exact: true }).fill("Hallway");
  await panel.getByRole("combobox", { name: "Room 5 condition" }).selectOption("missing");
  await panel.getByLabel("Room 5 note").fill("Smoke detector gone");
  await panel.getByRole("button", { name: "Record inspection" }).click();
  await expect(panel).toContainText(`The move-out inspection of ${today()} is recorded.`);
  await expect(panel).toContainText(`Move-out, ${today()}: 5 rooms, 2 damaged or missing`);
  await expect(panel).toContainText("by Demo Manager");

  await downloads(page, "Inspection report (PDF)", PDF);
  const href = (await panel.getByRole("link", { name: "Inspection report (PDF)" }).getAttribute("href")) ?? "";
  const csv = await (await page.request.get(href.replace("format=pdf", "format=csv"))).text();
  expect(csv).toContain(`inspected,,${firstOfMonthsAgo(11)},${today()},,`);
  expect(csv).toContain("room,Kitchen,good,damaged,true,move-out: Burn on the counter");
  expect(csv).toContain("room,Bathroom,good,good,false,");
  expect(csv).toContain("room,Hallway,,missing,,move-out: Smoke detector gone");
});

test("staff see the company's fees left in trust on Today and take them out to the company, once", async ({
  page,
}) => {
  const people = seed.features["company-fees"]!;
  await joinAsStaff(page, people.manager);

  // The demo took two months of management fees and left them in trust: Today says so, with
  // the day they were to be taken out by.
  await section(page, "Today");
  const waiting = page.getByRole("region", { name: "Company fees in trust" });
  await expect(waiting).toContainText("Demo operating trust account: $");
  await expect(waiting).toContainText("of the company's fees, the oldest since");
  await waiting.getByRole("link", { name: "Open Owners" }).click();

  const operating = page.getByRole("listitem", { name: "Demo operating trust account" });
  const held = operating.getByTestId("company-fees");
  await expect(held).toContainText("of the company's fees, the oldest since");
  const before = Number(((await held.textContent()) ?? "").match(/^\$([\d,]+\.\d\d)/)?.[1]?.replace(/,/g, ""));
  expect(before).toBeGreaterThan(10);
  await expect(operating.getByText(/^take out/)).toBeVisible();

  // More than is there is refused, in words; $10.00 comes out, then the rest.
  const asked = (before + 0.01).toFixed(2);
  await operating.getByLabel("Amount (leave empty for all)").fill(asked);
  await operating.getByRole("button", { name: "Take out company fees" }).click();
  await expect(operating.getByRole("alert")).toHaveText(
    `only $${before.toLocaleString("en-US", { minimumFractionDigits: 2 })} of the company's fees is in this trust bank account; $${Number(asked).toLocaleString("en-US", { minimumFractionDigits: 2 })} is more than that`,
  );
  await operating.getByLabel("Amount (leave empty for all)").fill("10.00");
  await operating.getByRole("button", { name: "Take out company fees" }).click();
  await expect(operating).toContainText("$10.00 of the company's fees taken out.");
  const rest = (before - 10).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  await expect(held).toContainText(`$${rest} of the company's fees`);
  await operating.getByRole("button", { name: "Take out company fees" }).click();
  await expect(operating).toContainText(`$${rest} of the company's fees taken out.`);
  await expect(held).toHaveText("None of the company's fees is held here.");
  await expect(operating.getByRole("button", { name: "Take out company fees" })).toHaveCount(0);

  // Nothing waits on Today any more.
  await section(page, "Today");
  await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
  await expect(page.getByRole("region", { name: "Company fees in trust" })).toHaveCount(0);
});

test("staff carry over the old system's unpaid bills, once, and draws hold them back", async ({ page }) => {
  const people = seed.features["cutover-bills"]!;
  await joinAsStaff(page, people.manager);
  await section(page, "Switching");
  const roster = part(page, "Bring the roster in");
  await roster.getByRole("combobox", { name: "File of" }).selectOption({ label: "Unpaid bills" });
  await expect(roster).toContainText("Approved by and Approved on");
  // One within Demo Owner B's $300.00 limit, one over it that the owner approved there.
  await roster.getByLabel("CSV file").setInputFiles({
    name: "unpaid-bills.csv",
    mimeType: "text/csv",
    buffer: Buffer.from(
      "Vendor,Owner,Property,Reference,Bill date,Due on,Amount,Memo,Approved by,Approved on\n" +
        `Demo Plumbing Co,Demo Owner B,Aspen Row,OLD-1,${firstOfMonthsAgo(1)},${daysFromToday(10)},120.00,Faucet,,\n` +
        `Demo Plumbing Co,Demo Owner B,Aspen Row,OLD-2,${firstOfMonthsAgo(1)},${daysFromToday(20)},400.00,Water heater,Demo Owner B,${firstOfMonthsAgo(1)}\n`,
    ),
  });
  await roster.getByRole("button", { name: "Import" }).click();
  await expect(roster).toContainText("2 added.");
  // Sent again: nothing twice.
  await roster.getByRole("button", { name: "Import" }).click();
  await expect(roster).toContainText("0 added; 2 already here.");

  // Aspen Row already held only its reserve; the bills are held back on top of it.
  await section(page, "Owners");
  const aspen = page.getByRole("listitem", { name: "Demo Owner B, Aspen Row" });
  await expect(aspen).toContainText("$200.00 held, less a $200.00 reserve and $520.00 of unpaid bills");
  await expect(aspen.getByTestId("available")).toHaveText("$0.00 available");
  await section(page, "Bills");
  await expect(page.getByText("OLD-2", { exact: false }).first()).toBeVisible();
});

test("an admin names the broker of a trust account, who authorizes a signer; then only signers pay out of it", async ({
  browser,
  page,
}) => {
  const people = seed.features.signers!;
  await joinAsStaff(page, people.manager);
  // No broker on file yet: Today asks for one.
  await section(page, "Today");
  const waiting = page.getByRole("region", { name: "Trust account signers" });
  await expect(waiting).toContainText("Demo operating trust account: no broker on file");

  await section(page, "Staff");
  const card = part(page, "Who signs each trust bank account");
  const operating = card.getByRole("listitem", { name: "Demo operating trust account" });
  await expect(operating).toContainText("No broker on file: an admin names the broker.");
  await card.getByLabel("Trust bank account").selectOption({ label: "Demo operating trust account" });
  await card.getByLabel("Staff member").selectOption({ label: "Demo Manager" });
  await card.getByLabel("As").selectOption("broker");
  await card.getByLabel("License number").fill("LIC-SYN-0001");
  await card.getByRole("button", { name: "Record" }).click();
  await expect(card).toContainText("Demo Manager is the broker of Demo operating trust account.");
  await expect(operating).toContainText("Demo Manager, the broker: license LIC-SYN-0001");

  // The bookkeeper is given pay-out, but signs nothing: a draw by them is refused, in words.
  await page.getByLabel("Demo Bookkeeper: Pay out").click();
  await expect(page.getByText("Demo Bookkeeper may now: Pay out.")).toBeVisible();
  const bookkeeper = await browser.newPage();
  await joinAsStaff(bookkeeper, people.bookkeeper);
  await section(bookkeeper, "Owners");
  const aspen = bookkeeper.getByRole("listitem", { name: "Demo Owner B, Aspen Row" });
  await aspen.getByText("Record for Demo Owner B, Aspen Row").click();
  await aspen.getByLabel("Contribution received", { exact: true }).fill("50.00");
  await aspen.getByRole("button", { name: "Record contribution" }).click();
  await expect(aspen).toContainText("$50.00 from Demo Owner B recorded.");
  await aspen.getByLabel("Draw amount (leave empty for all available)").fill("10.00");
  await aspen.getByRole("button", { name: "Record draw" }).click();
  await expect(aspen.getByRole("alert")).toHaveText(
    "Demo Bookkeeper is not an authorized signer of Demo operating trust account",
  );

  // The broker authorizes them, bonded; their draw then goes through.
  await card.getByLabel("Staff member").selectOption({ label: "Demo Bookkeeper" });
  await card.getByLabel("As").selectOption("signer");
  await card.getByLabel("Or bonded for").fill("25000.00");
  await card.getByLabel("Bond runs out").fill(daysFromToday(20));
  await card.getByRole("button", { name: "Record" }).click();
  await expect(card).toContainText("Demo Bookkeeper signs Demo operating trust account.");
  await expect(operating).toContainText("bond runs out in 20 days");
  await aspen.getByRole("button", { name: "Record draw" }).click();
  await expect(aspen).toContainText("$10.00 paid out to Demo Owner B.");
  await bookkeeper.close();
});
