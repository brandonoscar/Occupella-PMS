// Each feature of the portal used end to end in a real browser, on a demo PMC's books of its own
// (demo.books in src/pms/demo.py, seeded by e2e/seed.py): two months of rent, bills, fees, draws
// and month-end, last month prepared and waiting for its approval. ci/features.toml names the test that proves
// each feature, and tools/check_features.py keeps the two in step.
import { readFileSync } from "node:fs";
import { expect, type Locator, type Page, test } from "@playwright/test";
import { expectAccessible } from "./a11y";
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
// A day as the portal shows it: MM/DD/YYYY.
const us = (iso: string) => `${iso.slice(5, 7)}/${iso.slice(8, 10)}/${iso.slice(0, 4)}`;
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
  await expectAccessible(page);
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
  await page.getByRole("navigation", { name: "Sections" }).getByRole("link", { name, exact: true }).click();
  await expect(page.getByRole("heading", { level: 1, name, exact: true })).toBeVisible();
}

// Records a bill from Bills' side panel (Bills open already); `more` fills anything else.
async function recordBill(
  page: Page,
  vendor: string,
  account: string,
  reference: string,
  amount: string,
  memo: string,
  more?: (panel: Locator) => Promise<void>,
): Promise<void> {
  await page.getByRole("button", { name: "Record bill" }).click();
  const panel = page.getByRole("dialog", { name: "Record bill" });
  await panel.getByRole("combobox", { name: "Vendor", exact: true }).selectOption({ label: vendor });
  await panel.getByRole("combobox", { name: "Owner and property", exact: true }).selectOption({ label: account });
  await panel.getByLabel("Ref #").fill(reference);
  await panel.getByLabel("Amount", { exact: true }).fill(amount);
  await panel.getByLabel("Memo").fill(memo);
  await more?.(panel);
  await panel.getByRole("button", { name: "Record bill" }).click();
  await expect(page.getByText(`Bill ${reference} recorded.`)).toBeVisible();
  await expect(panel).toHaveCount(0);
}

// A bill's row on Bills, and one of its next steps from the row's ⋯ menu, in its side panel.
const billRow = (page: Page, vendor: string, reference: string) =>
  page.getByRole("row", { name: `${vendor} ${reference}`, exact: true });
async function billStep(page: Page, vendor: string, reference: string, step: string): Promise<Locator> {
  await billRow(page, vendor, reference).getByRole("button", { name: `Actions for ${vendor} ${reference}` }).click();
  await page.getByRole("menuitem", { name: step }).click();
  return page.getByRole("dialog", { name: step });
}

// A lease's own page, from the rent roll: the unit's link on the lease's row. Returns its rent work.
async function openLease(page: Page, name: string) {
  await section(page, "Rent roll");
  await page.getByRole("row", { name, exact: true }).getByRole("link", { name: name.split(", ")[1], exact: true }).click();
  const [property, unit] = name.split(", ");
  await expect(page.getByRole("heading", { level: 1, name: new RegExp(`^${property} · ${unit} · `) })).toBeVisible();
  return page.getByRole("main");
}

// One of a lease's side panels: Receive payment and Enter charge at its top, the rest in its ⋯ menu.
async function leasePanel(page: Page, name: string) {
  if (name === "Receive payment" || name === "Enter charge") {
    await page.getByRole("button", { name, exact: true }).click();
  } else {
    await page.getByRole("button", { name: "Actions for this lease" }).click();
    await page.getByRole("menuitem", { name, exact: true }).click();
  }
  const panel = page.getByRole("dialog", { name });
  await expect(panel).toBeVisible();
  return panel;
}

// A lease page's tab.
async function leaseTab(page: Page, name: string) {
  await page.getByRole("tab", { name, exact: true }).click();
}

// An owner's own page, from Owners: their name's link on their row.
async function openOwner(page: Page, name: string): Promise<void> {
  await section(page, "Owners");
  await page.getByRole("row", { name, exact: true }).getByRole("link", { name }).click();
  await expect(page.getByRole("heading", { level: 1, name, exact: true })).toBeVisible();
}

// A property's own page, from Properties: its name's link on its row.
async function openProperty(page: Page, name: string): Promise<void> {
  await section(page, "Properties");
  await page.getByRole("row", { name, exact: true }).getByRole("link", { name, exact: true }).click();
  await expect(page.getByRole("heading", { level: 1, name, exact: true })).toBeVisible();
}

// One of a page's setup side panels, opened by its button.
async function setupPanel(page: Page, button: string, title = button, scope: Page | Locator = page) {
  await scope.getByRole("button", { name: button, exact: true }).click();
  const panel = page.getByRole("dialog", { name: title });
  await expect(panel).toBeVisible();
  return panel;
}

const DOLLARS = new Intl.NumberFormat("en-US", { style: "currency", currency: "USD" });

// Record contribution, from an owner's Financials tab, into one of their properties.
async function recordContribution(page: Page, owner: string, property: string, amount: string, memo = "") {
  await openOwner(page, owner);
  await page.getByRole("tab", { name: "Financials", exact: true }).click();
  await page.getByRole("button", { name: "Record contribution" }).click();
  const panel = page.getByRole("dialog", { name: "Record contribution" });
  await panel.getByLabel("Into").selectOption({ label: property });
  await panel.getByLabel("Contribution received").fill(amount);
  if (memo) await panel.getByLabel("For", { exact: true }).fill(memo);
  await panel.getByRole("button", { name: "Record contribution" }).click();
  await expect(panel).toHaveCount(0);
  await expect(page.getByRole("main")).toContainText(`${DOLLARS.format(Number(amount))} from ${owner} recorded.`);
}

// An owner's property account on their Properties tab: what it holds, keeps back and may pay out.
async function ownerProperty(page: Page, owner: string, property: string) {
  await openOwner(page, owner);
  await page.getByRole("tab", { name: "Properties", exact: true }).click();
  return page.getByRole("table", { name: "Properties" }).getByRole("row", { name: property, exact: true });
}

// Pay owners: one owner's property account, an amount (everything available unless given),
// after confirming. Returns the row, whose Available cell is its seventh.
async function payOwner(page: Page, owner: string, property: string, amount = "") {
  await section(page, "Pay owners");
  const row = page.getByRole("row", { name: `${owner}, ${property}`, exact: true });
  await row.getByLabel(`Pay ${owner}, ${property}`).check();
  if (amount) await row.getByLabel(`Amount to pay ${owner}, ${property}`).fill(amount);
  await page.getByRole("button", { name: /^Pay \$/ }).click();
  await page.getByRole("alertdialog").getByRole("button", { name: "Confirm" }).click();
  return row;
}

// Journal entries, from the general ledger's Journal entry button.
async function journal(page: Page): Promise<void> {
  await section(page, "General ledger");
  await page.getByRole("link", { name: "Journal entry", exact: true }).click();
  await expect(page.getByRole("heading", { level: 1, name: "Journal entries", exact: true })).toBeVisible();
}

// A record's ⋯ menu: opens it, picks the action and confirms it in its dialog.
async function chooseAction(page: Page, record: string, action: string): Promise<void> {
  await page.getByRole("button", { name: `Actions for ${record}` }).click();
  await page.getByRole("menuitem", { name: action }).click();
  await page.getByRole("alertdialog").getByRole("button", { name: action, exact: true }).click();
}

// One of Settings' pages: Settings in the menu, then the page.
async function settings(page: Page, name: string): Promise<void> {
  await section(page, "Settings");
  await page.getByRole("list", { name: "Settings" }).getByRole("link", { name, exact: true }).click();
  await expect(page.getByRole("heading", { level: 1, name, exact: true })).toBeVisible();
}

test("staff charge this month's rent and its late fees, and the Dashboard shows the rent that is late", async ({
  page,
}) => {
  const people = seed.features.rent!;
  await joinAsStaff(page, people.manager);

  // Last month waits for its approval on both trust bank accounts.
  await section(page, "Dashboard");
  const monthEnd = page.getByRole("region", { name: "Month-end" });
  await expect(monthEnd).toContainText("Demo operating trust account");
  await expect(monthEnd).toContainText("Demo security deposit account");

  // Rent posts by itself every night (off for these tests); an admin runs it by hand from the
  // rent roll's ⋯ menu: this month's rent and recurring charges, and rent unpaid past five
  // days' grace takes a $50.00 late fee.
  await section(page, "Rent roll");
  const runRent = async () => {
    await page.getByRole("button", { name: "Actions for the rent roll" }).click();
    await page.getByRole("menuitem", { name: "Run rent now" }).click();
    const panel = page.getByRole("dialog", { name: "Run rent now" });
    await expect(panel.getByLabel("Rent due on")).toHaveValue(firstOfThisMonth());
    await panel.getByLabel("Late fees as of").fill(today());
    await panel.getByRole("button", { name: "Run rent now" }).click();
    await expect(panel).toHaveCount(0);
  };
  const late = dayOfMonth() > 6;
  await runRent();
  await expect(page.getByRole("main")).toContainText(
    `6 rents, 1 recurring charge and ${late ? 6 : 0} late fee${late ? "s" : ""} charged.`,
  );
  // Once per lease and due date: the same run again charges nothing, and it is listed.
  await runRent();
  await expect(page.getByRole("main")).toContainText("0 rents, 0 recurring charges and 0 late fees charged.");
  await page.getByRole("button", { name: "Actions for the rent roll" }).click();
  await page.getByRole("menuitem", { name: "Run rent now" }).click();
  // The demo's own four (two a month), then these two, newest first.
  const runs = page.getByRole("list", { name: "Latest rent runs" }).getByRole("listitem");
  await expect(runs).toHaveCount(6);
  await expect(runs.first()).toContainText("Demo Manager: 0 rents, 0 recurring charges and 0 late fees charged.");
  await page.getByRole("dialog", { name: "Run rent now" }).getByRole("button", { name: "Close" }).click();
  const lease = (name: string) => page.getByRole("row", { name, exact: true });
  await expect(lease("Aspen Row, Unit 1").getByTestId("balance")).toHaveText(late ? "$1,230.00" : "$1,180.00");
  // Birch Flats Unit A owes its pet rent with its rent.
  await expect(lease("Birch Flats, Unit A").getByTestId("balance")).toHaveText(late ? "$1,480.00" : "$1,430.00");

  // Receive payment finds the lease by its tenant, unit or property.
  await page.getByRole("button", { name: "Receive payment", exact: true }).click();
  const pay = page.getByRole("dialog", { name: "Receive payment" });
  await pay.getByLabel("Lease").fill("Aspen Row");
  await pay.getByRole("list", { name: "Leases found" }).getByRole("button", { name: /^Aspen Row · Unit 1 · / }).click();
  await pay.getByLabel("Payment received").fill("100.00");
  await pay.getByLabel("Method").selectOption("EFT");
  await pay.getByRole("button", { name: "Record payment" }).click();
  await expect(pay).toHaveCount(0);
  await expect(page.getByRole("main")).toContainText("$100.00 paid what is owed.");
  await expect(lease("Aspen Row, Unit 1").getByTestId("balance")).toHaveText(late ? "$1,130.00" : "$1,080.00");

  // The Dashboard counts what the six leases owe (and the late fees, less the $100.00 paid), and
  // the one vacant unit.
  await section(page, "Dashboard");
  const figures = page.getByRole("list", { name: "Figures" });
  const owing = figures.getByRole("link", { name: /Outstanding balances/ });
  await expect(owing).toContainText(late ? "$9,645.00" : "$9,345.00");
  await expect(owing).toContainText("6 leases owe");
  await expect(figures.getByRole("link", { name: /Vacant units/ })).toContainText("1no lease in effect today");
  // Needs attention lists the rent due before today and not paid, with the way to the rent roll.
  if (dayOfMonth() > 1) {
    const lateRent = page.getByRole("region", { name: "Late rent" });
    await expect(lateRent.getByRole("listitem")).toHaveCount(6);
    await lateRent.getByRole("link", { name: "Open Rent roll" }).click();
    await expect(page.getByRole("heading", { level: 1, name: "Rent roll" })).toBeVisible();
  } else {
    await expect(page.getByRole("region", { name: "Late rent" })).toHaveCount(0);
  }

  // A payment pays rent first, then the monthly charges, then other fees: Birch Flats Unit A owes
  // a key fee from last month, older than its rent, yet $1,415.00 pays all its rent and $20.00 of
  // its pet rent, and the key fee waits.
  const lastMonthEnd = new Date(Date.UTC(Number(today().slice(0, 4)), Number(today().slice(5, 7)) - 1, 0))
    .toISOString()
    .slice(0, 10);
  const unitA = await openLease(page, "Birch Flats, Unit A");
  const charge = await leasePanel(page, "Enter charge");
  await charge.getByLabel("Add a").selectOption("fee");
  await charge.getByLabel("Amount", { exact: true }).fill("25.00");
  await charge.getByLabel("Due on").fill(lastMonthEnd);
  await charge.getByLabel("What for").fill("Key replacement");
  await charge.getByRole("button", { name: "Add", exact: true }).click();
  await expect(unitA).toContainText("Fee of $25.00 added.");
  const paying = await leasePanel(page, "Receive payment");
  await paying.getByLabel("Payment received").fill("1415.00");
  await paying.getByLabel("Check or reference").fill("Check 7001");
  await paying.getByRole("button", { name: "Record payment" }).click();
  await expect(unitA).toContainText("$1,415.00 paid what is owed.");
  const open = unitA.getByRole("list", { name: "Open charges" });
  await expect(open).toContainText(`${us(lastMonthEnd)} Key replacement: $25.00 open`);
  await expect(open).toContainText(`Pet rent due ${firstOfThisMonth()}: $15.00 open of $35.00`);
  await expect(open).not.toContainText(`Rent due ${firstOfThisMonth()}`);
  await expect(open.getByRole("listitem")).toHaveCount(late ? 3 : 2);

  // Aspen Row Unit 1 pays the rest of what it owes, so the rent roll's Owing list leaves it out,
  // narrowed to a property with the picker; the Delinquency report on screen narrows the same way.
  await openLease(page, "Aspen Row, Unit 1");
  const rest = await leasePanel(page, "Receive payment");
  await rest.getByLabel("Payment received").fill(late ? "1130.00" : "1080.00");
  await rest.getByRole("button", { name: "Record payment" }).click();
  await expect(page.getByTestId("balance-due")).toHaveText("$0.00");
  await section(page, "Rent roll");
  await page.getByRole("combobox", { name: "Status" }).selectOption({ label: "Owing" });
  const rows = page.getByRole("table").getByRole("row");
  await expect(rows).toHaveCount(6);
  await expect(lease("Aspen Row, Unit 1")).toHaveCount(0);
  await expect(lease("Birch Flats, Unit A").getByTestId("balance")).toHaveText(late ? "$90.00" : "$40.00");
  const shown = page.getByRole("combobox", { name: "Properties shown" });
  await shown.selectOption({ label: "Aspen Row" });
  await expect(page.getByTestId("empty-state")).toContainText("No leases found.");
  await shown.selectOption({ label: "Birch Flats" });
  await expect(rows).toHaveCount(3);
  await section(page, "Reports");
  await page.getByRole("list", { name: "Rentals" }).getByRole("link", { name: "Delinquency" }).click();
  await expect(page.getByRole("heading", { level: 1, name: "Delinquency" })).toBeVisible();
  const delinquency = page.getByRole("table", { name: "Delinquency" });
  await expect(delinquency).toContainText("Unit A");
  await expect(delinquency).toContainText("Unit B");
  await expect(delinquency).not.toContainText("Elm Court");
  await page.getByRole("combobox", { name: "Properties shown" }).selectOption({ label: "All properties" });
  await expect(delinquency).toContainText("Elm Court");
});

test("staff find a property, rename it and a unit, archive a vacant unit but not a property with a lease, and search for a tenant", async ({
  page,
}) => {
  const people = seed.features.rentals!;
  await joinAsStaff(page, people.manager);

  // Properties, a page at a time, each with its owners and how full it is.
  await section(page, "Properties");
  const aspen = page.getByRole("row", { name: "Aspen Row", exact: true });
  await expect(aspen).toContainText("Demo Owner B");
  await expect(aspen).toContainText("1 of 2 occupied");
  await expect(page.getByRole("navigation", { name: "Pages" })).toContainText("1–4 of 4");
  const shown = page.getByRole("combobox", { name: "Properties shown" });
  await shown.selectOption({ label: "Birch Flats" });
  await expect(page.getByRole("row", { name: "Birch Flats", exact: true })).toBeVisible();
  await expect(aspen).toHaveCount(0);
  await shown.selectOption({ label: "All properties" });

  // A property: what its owner's account holds, and each unit's state.
  await aspen.getByRole("link", { name: "Aspen Row" }).click();
  await expect(page.getByRole("heading", { level: 1, name: "Aspen Row" })).toBeVisible();
  await expect(part(page, "Owners")).toContainText("Demo Owner B");
  const unit = (name: string) => page.getByRole("row", { name, exact: true });
  await expect(unit("Unit 1")).toContainText("Occupied");
  await expect(unit("Unit 1")).toContainText("Drew Sample");
  await expect(unit("Unit 2")).toContainText("Vacant");

  // A property with a lease in effect stays; the refusal says why, in words.
  await chooseAction(page, "Aspen Row", "Archive");
  await expect(page.getByRole("alert")).toHaveText(
    "Aspen Row has a lease in effect or to come; end it before archiving",
  );
  await page.getByRole("button", { name: "Edit", exact: true }).click();
  await page.getByLabel("Name", { exact: true }).fill("Aspen Row East");
  await page.getByRole("button", { name: "Save", exact: true }).click();
  await expect(page.getByText("Renamed Aspen Row East.")).toBeVisible();
  await expect(page.getByRole("heading", { level: 1, name: "Aspen Row East" })).toBeVisible();

  // The vacant unit: renamed, archived (it leaves the property's list as archived), brought back.
  await unit("Unit 2").getByRole("link", { name: "Unit 2" }).click();
  await expect(page.getByRole("heading", { level: 1, name: "Unit 2, Aspen Row East" })).toBeVisible();
  await expect(page.getByTestId("empty-state")).toContainText("No lease yet.");
  await page.getByRole("button", { name: "Edit", exact: true }).click();
  await page.getByLabel("Name", { exact: true }).fill("Unit 2B");
  await page.getByRole("button", { name: "Save", exact: true }).click();
  await expect(page.getByRole("heading", { level: 1, name: "Unit 2B, Aspen Row East" })).toBeVisible();
  await chooseAction(page, "Unit 2B", "Archive");
  await expect(page.getByText("Unit 2B is archived.")).toBeVisible();
  await page.getByRole("main").getByRole("link", { name: "Aspen Row East" }).click();
  await expect(unit("Unit 2B")).toContainText("Archived");
  await unit("Unit 2B").getByRole("link", { name: "Unit 2B" }).click();
  await chooseAction(page, "Unit 2B", "Bring back");
  await expect(page.getByText("Unit 2B is back.")).toBeVisible();

  // Search, from any page: a tenant opens their page and their lease from it, a property its page.
  const search = page.getByLabel("Search people, homes, bills, work orders and files");
  await search.fill("drew");
  await page.getByRole("list", { name: "Search results" }).getByRole("link", { name: /Drew Sample/ }).click();
  await expect(page.getByRole("heading", { level: 1, name: "Drew Sample" })).toBeVisible();
  await page.getByRole("tab", { name: "Leases", exact: true }).click();
  await page.getByRole("row", { name: "Aspen Row East, Unit 1" }).getByRole("link").click();
  await expect(page.getByRole("heading", { level: 1, name: /^Aspen Row East · Unit 1 · .*Drew Sample/ })).toBeVisible();
  // Its tenants have pages of their own, a click away.
  await page.getByRole("tab", { name: "Tenants", exact: true }).click();
  await expect(page.getByRole("table", { name: "Tenants" }).getByRole("row", { name: "Drew Sample" })).toBeVisible();
  // A note on the lease stays with it.
  await page.getByRole("tab", { name: "Notes", exact: true }).click();
  await page.getByLabel("New note").fill("Asked for a second parking space.");
  await page.getByRole("button", { name: "Add note" }).click();
  await expect(page.getByRole("list", { name: /^Notes/ })).toContainText("Asked for a second parking space.");
  await search.fill("cedar");
  await expect(page.getByRole("list", { name: "Search results" })).toContainText("Cedar House");
  await search.press("Enter");
  await expect(page.getByRole("heading", { level: 1, name: "Cedar House" })).toBeVisible();
});

test("staff group properties, and every list shows just the group's, the choice carried from page to page", async ({
  page,
}) => {
  const people = seed.features.groups!;
  await joinAsStaff(page, people.manager);

  // Settings, Property groups: a group of two properties, added in a side panel.
  await settings(page, "Property groups");
  await expect(page.getByTestId("empty-state")).toContainText("No property groups yet.");
  await page.getByRole("button", { name: "Add group" }).click();
  const panel = page.getByRole("dialog", { name: "Add group" });
  await panel.getByLabel("Name", { exact: true }).fill("North portfolio");
  await panel.getByLabel("Birch Flats").check();
  await panel.getByLabel("Cedar House").check();
  await panel.getByRole("button", { name: "Add group" }).click();
  await expect(page.getByText("Added the group North portfolio, with 2 properties.")).toBeVisible();
  await expect(page.getByRole("row", { name: "North portfolio" })).toContainText("Birch Flats, Cedar House");

  // Properties narrowed to the group, and the rent roll, tenants and owners open on it too.
  await section(page, "Properties");
  const shown = page.getByRole("combobox", { name: "Properties shown" });
  await shown.selectOption({ label: "North portfolio" });
  await expect(page.getByRole("row", { name: "Aspen Row", exact: true })).toHaveCount(0);
  await expect(page.getByRole("row", { name: "Birch Flats", exact: true })).toBeVisible();
  await expect(page.getByRole("row", { name: "Cedar House", exact: true })).toBeVisible();
  await section(page, "Rent roll");
  await expect(page).toHaveURL(/[?&]group=/);
  await expect(page.getByRole("row", { name: /^Cedar House, / }).first()).toBeVisible();
  await expect(page.getByRole("row", { name: /^Aspen Row, / })).toHaveCount(0);
  await section(page, "Tenants");
  await expect(page.getByRole("row", { name: "Taylor Demo", exact: true })).toBeVisible();
  await expect(page.getByRole("row", { name: "Drew Sample", exact: true })).toHaveCount(0);
  await section(page, "Owners");
  await expect(page.getByRole("row", { name: "Demo Owner A", exact: true })).toBeVisible();
  await expect(page.getByRole("row", { name: "Demo Owner B", exact: true })).toHaveCount(0);

  // The group renamed and changed; one property is a choice too, and All properties ends it.
  await settings(page, "Property groups");
  await page.getByRole("button", { name: "North portfolio" }).click();
  const edit = page.getByRole("dialog", { name: "Edit North portfolio" });
  await edit.getByLabel("Name", { exact: true }).fill("North");
  await edit.getByLabel("Cedar House").uncheck();
  await edit.getByRole("button", { name: "Save", exact: true }).click();
  await expect(page.getByText("Saved North: 1 property.")).toBeVisible();
  await section(page, "Properties");
  await expect(shown).toHaveValue(/^group:/);
  await expect(page.getByRole("row", { name: "Cedar House", exact: true })).toHaveCount(0);
  await shown.selectOption({ label: "Aspen Row" });
  await expect(page.getByRole("row", { name: "Aspen Row", exact: true })).toBeVisible();
  await expect(page.getByRole("row", { name: "Birch Flats", exact: true })).toHaveCount(0);
  await shown.selectOption({ label: "All properties" });
  await expect(page.getByRole("navigation", { name: "Pages" })).toContainText("1–4 of 4");
});

test("staff read each account's general ledger, open a trust bank account from Banking with its register and what it holds, and see what each owner may be paid", async ({
  page,
}) => {
  const people = seed.features["general-ledger"]!;
  await joinAsStaff(page, people.manager);

  // The general ledger, from two months back to today: each account's entries and running balance.
  await section(page, "General ledger");
  await page.getByLabel("From").fill(firstOfMonthsAgo(2));
  await page.getByLabel("Find an account").fill("aspen row");
  const aspen = page.getByRole("table", { name: "owner_property: Demo Owner B / Aspen Row, Demo operating trust account (operating)" });
  await expect(aspen.getByRole("row").first()).toBeVisible();
  await expect(aspen.getByRole("row", { name: /^Prior balance/ })).toBeVisible();
  await expect(aspen.getByRole("row", { name: /^Total/ })).toBeVisible();
  await expect(page.getByRole("table")).toHaveCount(1);
  const pdf = await page.request.get(
    (await page.getByRole("link", { name: "PDF", exact: true }).getAttribute("href")) ?? "",
  );
  expect(pdf.status()).toBe(200);

  // Banking: each trust bank account with its book balance, opening its own page.
  await section(page, "Banking");
  const operating = page.getByRole("row", { name: "Demo operating trust account", exact: true });
  await expect(operating).toContainText("Operating");
  await operating.getByRole("link").click();
  await expect(page.getByRole("heading", { level: 1, name: "Demo operating trust account" })).toBeVisible();
  await expect(page.getByTestId("book-cash")).toContainText("Book balance $");
  // Last month waits for approval, so it opens on Reconciliations; its register is Transactions:
  // each move of its cash with what it was and the balance after it.
  await expect(page.getByRole("tab", { name: "Reconciliations" })).toHaveAttribute("aria-selected", "true");
  await page.getByRole("tab", { name: "Transactions" }).click();
  const register = page.getByRole("table", { name: "Register" });
  await expect(register.getByRole("row", { name: /Rent payment/ }).first()).toBeVisible();
  await expect(register).toContainText("Rent – ");
  // What it holds, by kind, comes to its book cash.
  await page.getByRole("tab", { name: "Balance breakdown" }).click();
  const cash = ((await page.getByTestId("book-cash").textContent()) ?? "").replace("Book balance ", "");
  await expect(page.getByTestId("breakdown-cash")).toHaveText(cash);
  await expect(page.getByRole("table", { name: "By owner" }).getByRole("row", { name: "Demo Owner B, Aspen Row" })).toBeVisible();

  // Pay owners: what each owner's account may pay out; one holding only its reserve can't be chosen.
  await section(page, "Pay owners");
  const birch = page.getByRole("row", { name: "Demo Owner A, Birch Flats", exact: true });
  await expect(birch).toContainText("$250.00");
  const reserved = page.getByRole("row", { name: "Demo Owner B, Aspen Row", exact: true });
  await expect(reserved.getByRole("cell").nth(6)).toHaveText("$0.00");
  await expect(reserved.getByLabel("Pay Demo Owner B, Aspen Row")).toBeDisabled();
});

test("staff open a work order with its details, schedule its vendor (not one whose insurance ran out), bill it, find it on the vendor's page, rename the vendor, and see the leases to renew", async ({
  page,
}) => {
  const people = seed.features["maintenance-pages"]!;
  await joinAsStaff(page, people.manager);

  // The plumber's only policy ran out yesterday.
  await section(page, "Vendors");
  await page.getByRole("row", { name: "Demo Plumbing Co", exact: true }).click();
  await page.getByRole("tab", { name: "Insurance" }).click();
  await page.getByRole("button", { name: "Add policy" }).click();
  const policy = page.getByRole("dialog", { name: "Add policy" });
  await policy.getByRole("combobox", { name: "Coverage" }).selectOption("general liability");
  await policy.getByLabel("Carrier").fill("Synthetic Mutual");
  await policy.getByLabel("Policy number").fill("GL-0009");
  await policy.getByLabel("Starts").fill(firstOfMonthsAgo(11));
  await policy.getByLabel("Expires").fill(daysFromToday(-1));
  await policy.getByRole("button", { name: "Record policy" }).click();
  await expect(page.getByText("Demo Plumbing Co's general liability policy GL-0009 is recorded.")).toBeVisible();

  // A work order staff open themselves, for a unit, urgent and due tomorrow; it opens its page.
  await section(page, "Work orders");
  await page.getByRole("button", { name: "New work order" }).click();
  const panel = page.getByRole("dialog", { name: "New work order" });
  await panel.getByRole("combobox", { name: "Property", exact: true }).selectOption({ label: "Aspen Row" });
  await panel.getByRole("combobox", { name: "Unit", exact: true }).selectOption({ label: "Unit 2" });
  await panel.getByLabel("Title").fill("Window latch");
  await panel.getByRole("combobox", { name: "Priority" }).selectOption({ label: "Urgent" });
  await panel.getByLabel("Due (optional)").fill(daysFromToday(1));
  await panel.getByLabel("What is wrong (optional)").fill("The bedroom window won't lock.");
  await panel.getByRole("combobox", { name: "Entry" }).selectOption({ label: "Call the tenant before going in" });
  await panel.getByRole("button", { name: "Open work order" }).click();
  await expect(page.getByRole("heading", { level: 1, name: /Window latch$/ })).toBeVisible();
  await expect(page.getByTestId("status")).toHaveText("New");
  await expect(page.getByTestId("priority")).toHaveText("Urgent");
  await expect(page.getByLabel("Summary")).toContainText(us(daysFromToday(1)));
  await expect(page.getByTestId("description")).toHaveText("The bedroom window won't lock.");

  // Its details change in a side panel.
  await page.getByRole("button", { name: "Edit details" }).click();
  const details = page.getByRole("dialog", { name: "Edit details" });
  await details.getByRole("combobox", { name: "Priority" }).selectOption({ label: "High" });
  await details.getByRole("button", { name: "Save" }).click();
  await expect(page.getByText("Saved the details.")).toBeVisible();
  await expect(page.getByTestId("priority")).toHaveText("High");

  // The plumber can't be assigned until their insurance is renewed; the roofer can.
  await page.getByRole("button", { name: "Next step" }).click();
  let next = page.getByRole("dialog", { name: "Next step" });
  await next.getByRole("combobox", { name: "Vendor", exact: true }).selectOption({ label: "Demo Plumbing Co" });
  await next.getByRole("button", { name: "Record" }).click();
  await expect(next.getByRole("alert")).toHaveText(
    `the vendor's insurance expired on ${us(daysFromToday(-1))}; record its renewal before assigning them`,
  );
  await next.getByRole("combobox", { name: "Vendor", exact: true }).selectOption({ label: "Demo Roofing Co" });
  await next.getByLabel("Note (optional)").fill("Tuesday morning");
  await next.getByRole("button", { name: "Record" }).click();
  await expect(page.getByText("Assigned to Demo Roofing Co.")).toBeVisible();
  await expect(page.getByTestId("status")).toHaveText("Assigned");

  // Then the visit is scheduled.
  await page.getByRole("button", { name: "Next step" }).click();
  next = page.getByRole("dialog", { name: "Next step" });
  await next.getByRole("combobox", { name: "What happens next" }).selectOption({ label: "Schedule the visit" });
  await next.getByLabel("Visit on").fill(daysFromToday(2));
  await next.getByRole("button", { name: "Record" }).click();
  await expect(page.getByText(`Scheduled for ${us(daysFromToday(2))}.`)).toBeVisible();
  await expect(page.getByTestId("status")).toHaveText("Scheduled");
  const timeline = page.getByRole("list", { name: "Timeline" });
  await expect(timeline).toContainText("Assigned to Demo Roofing Co: Tuesday morning");
  await expect(timeline).toContainText(`Scheduled for ${us(daysFromToday(2))}`);

  // A photo of it, and the roofer's bill naming it, its vendor and property filled in.
  await page.getByLabel("Work order photo file").setInputFiles({
    name: "latch.png", mimeType: "image/png", buffer: Buffer.from("89504e470d0a1a0a0000000d49484452", "hex"),
  });
  await page.getByRole("button", { name: "Upload" }).click();
  await expect(page.getByText("Uploaded latch.")).toBeVisible();
  await expect(page.getByRole("list", { name: "Files, Window latch" })).toContainText("latch");
  await page.getByRole("button", { name: "Create bill" }).click();
  const bill = page.getByRole("dialog", { name: "Create bill" });
  await expect(bill.getByRole("combobox", { name: "Vendor", exact: true })).toHaveValue(/.+/);
  await bill.getByLabel("Ref #").fill("RF-LATCH");
  await bill.getByLabel("Amount").fill("45.00");
  await bill.getByRole("button", { name: "Record bill" }).click();
  await expect(page.getByText("Bill RF-LATCH recorded.")).toBeVisible();
  await expect(page.getByRole("row", { name: "RF-LATCH" })).toContainText("Demo Roofing Co");

  // On the list: its number, priority and status; narrowed to the scheduled ones.
  await section(page, "Work orders");
  await page.reload();
  const latch = page.getByRole("row", { name: "Window latch", exact: true });
  await expect(latch).toContainText("Unit 2, Aspen Row");
  await expect(latch).toContainText("High");
  await page.getByRole("combobox", { name: "Status" }).selectOption({ label: "Scheduled" });
  await expect(page.getByRole("table").getByRole("row")).toHaveCount(2);
  await expect(latch).toContainText("Scheduled");
  // Narrowed to one property: Aspen Row's keeps it, Birch Flats' has none scheduled.
  const shown = page.getByRole("combobox", { name: "Properties shown" });
  await shown.selectOption({ label: "Aspen Row" });
  await expect(latch).toBeVisible();
  await shown.selectOption({ label: "Birch Flats" });
  await expect(page.getByTestId("empty-state")).toContainText("No work orders found.");
  await shown.selectOption({ label: "All properties" });
  await expect(latch).toBeVisible();

  // The vendor's page lists it and the bill; the vendor is renamed there.
  await section(page, "Vendors");
  await page.getByRole("row", { name: "Demo Roofing Co", exact: true }).click();
  await expect(page.getByRole("heading", { level: 1, name: "Demo Roofing Co" })).toBeVisible();
  await page.getByRole("tab", { name: "Work orders" }).click();
  await expect(page.getByRole("table", { name: "Work orders" })).toContainText("Window latch");
  await page.getByRole("tab", { name: "Bills" }).click();
  await expect(page.getByRole("table", { name: "Bills" })).toContainText("RF-LATCH");
  await page.getByRole("button", { name: "Edit", exact: true }).click();
  await page.getByLabel("Name", { exact: true }).fill("Demo Roofing Company");
  await page.getByRole("button", { name: "Save", exact: true }).click();
  await expect(page.getByRole("heading", { level: 1, name: "Demo Roofing Company" })).toBeVisible();

  // Renewals: the demo's leases run month to month, so none ends in the next 90 days.
  await section(page, "Renewals");
  await expect(page.getByTestId("empty-state")).toContainText("No lease ends in the next 90 days.");
  await expect(page.getByRole("link", { name: "Draft a renewal" })).toHaveAttribute("href", "/draft-leases");
});

test("staff find a tenant and an owner, keep their contact details and a vendor's, rename them, and archive only what has nothing open", async ({
  page,
}) => {
  const people = seed.features.people!;
  await joinAsStaff(page, people.manager);

  // Tenants, a page at a time, each with their unit, balance and whether they can sign in.
  await section(page, "Tenants");
  const drew = page.getByRole("row", { name: "Drew Sample", exact: true });
  await expect(drew).toContainText("Aspen Row");
  await expect(drew).toContainText("Unit 1");
  await expect(drew).toContainText("Invited");
  const shown = page.getByRole("combobox", { name: "Properties shown" });
  await shown.selectOption({ label: "Cedar House" });
  await expect(page.getByRole("row", { name: "Taylor Demo", exact: true })).toBeVisible();
  await expect(drew).toHaveCount(0);
  await shown.selectOption({ label: "All properties" });

  // A tenant's page: their contact details, saved whole, and their name.
  await drew.getByRole("link", { name: "Drew Sample" }).click();
  await expect(page.getByRole("heading", { level: 1, name: "Drew Sample" })).toBeVisible();
  const contact = page.getByRole("group", { name: "Contact details, Drew Sample" });
  await expect(contact).toContainText("No contact details yet.");
  await contact.getByRole("button", { name: "Edit contact details" }).click();
  await contact.getByLabel("Email").fill("drew.sample@example.com");
  await contact.getByLabel("Phone", { exact: true }).fill("(555) 555-0142");
  await contact.getByLabel("Street address").fill("1 Example Road");
  await contact.getByLabel("City").fill("Sampletown");
  await contact.getByLabel("State").fill("ZZ");
  await contact.getByLabel("ZIP code").fill("00000");
  await contact.getByRole("button", { name: "Save contact details" }).click();
  await expect(contact).toContainText("Contact details saved.");
  await expect(contact).toContainText("Sampletown, ZZ 00000");
  await page.getByRole("button", { name: "Edit", exact: true }).click();
  await page.getByLabel("Name", { exact: true }).fill("Drew Q. Sample");
  await page.getByRole("button", { name: "Save", exact: true }).click();
  await expect(page.getByRole("heading", { level: 1, name: "Drew Q. Sample" })).toBeVisible();
  // On a lease in effect, they stay; the refusal says why, in words.
  await chooseAction(page, "Drew Q. Sample", "Archive");
  await expect(page.getByRole("alert")).toHaveText(
    "Drew Q. Sample is on a lease in effect or to come; end it before archiving",
  );
  await section(page, "Tenants");
  await expect(page.getByRole("row", { name: "Drew Q. Sample", exact: true })).toContainText(
    "drew.sample@example.com",
  );
  // The search finds them by the phone on file, whatever its brackets and dashes (q135).
  const byPhone = page.getByLabel("Search people, homes, bills, work orders and files");
  await byPhone.fill("5550142");
  await expect(page.getByRole("list", { name: "Search results" }).getByRole("link")).toHaveText([/^Drew Q\. Sample.*Tenant$/]);
  await byPhone.fill("");

  // Owners, one row each with all their properties; an owner's page, renamed, with their details.
  await section(page, "Owners");
  const ownerA = page.getByRole("row", { name: "Demo Owner A", exact: true });
  await expect(ownerA).toContainText("Birch Flats, Cedar House, Elm Court");
  await ownerA.getByRole("link", { name: "Demo Owner A" }).click();
  await expect(page.getByRole("heading", { level: 1, name: "Demo Owner A" })).toBeVisible();
  await page.getByRole("tab", { name: "Properties" }).click();
  await expect(page.getByRole("table", { name: "Properties" }).getByRole("row", { name: "Cedar House" })).toBeVisible();
  await page.getByRole("tab", { name: "Summary" }).click();
  await page.getByRole("button", { name: "Edit", exact: true }).click();
  await page.getByLabel("Name", { exact: true }).fill("Demo Owner A Trust");
  await page.getByRole("button", { name: "Save", exact: true }).click();
  await expect(page.getByRole("heading", { level: 1, name: "Demo Owner A Trust" })).toBeVisible();
  const details = page.getByRole("group", { name: "Contact details, Demo Owner A Trust" });
  await details.getByRole("button", { name: "Edit contact details" }).click();
  await details.getByLabel("Email").fill("owner.a@example.com");
  await details.getByRole("button", { name: "Save contact details" }).click();
  await expect(details).toContainText("owner.a@example.com");
  // Their accounts hold money: they stay.
  await chooseAction(page, "Demo Owner A Trust", "Archive");
  await expect(page.getByRole("alert")).toHaveText(
    "Demo Owner A Trust holds their money; draw it before archiving",
  );

  // A vendor's contact details, on their page; the list shows the phone.
  await section(page, "Vendors");
  await page.getByRole("row", { name: "Demo Plumbing Co", exact: true }).click();
  const plumbing = page.getByRole("group", { name: "Contact details, Demo Plumbing Co" });
  await plumbing.getByRole("button", { name: "Edit contact details" }).click();
  await plumbing.getByLabel("Phone", { exact: true }).fill("555-555-0177");
  await plumbing.getByRole("button", { name: "Save contact details" }).click();
  await expect(plumbing).toContainText("Contact details saved.");
  await expect(plumbing).toContainText("555-555-0177");
  await section(page, "Vendors");
  await expect(page.getByRole("row", { name: "Demo Plumbing Co", exact: true })).toContainText("555-555-0177");

  // Search finds an owner too.
  const search = page.getByLabel("Search people, homes, bills, work orders and files");
  await search.fill("owner b");
  await page.getByRole("list", { name: "Search results" }).getByRole("link", { name: /Demo Owner B/ }).click();
  await expect(page.getByRole("heading", { level: 1, name: "Demo Owner B" })).toBeVisible();
});

test("staff add a fee, a credit and a monthly charge to a lease, take a deposit and a pet deposit and prepaid rent, and a bounce undoes a payment and charges the NSF fee", async ({
  page,
}) => {
  const people = seed.features.recurring!;
  await joinAsStaff(page, people.manager);

  // The property's NSF fee, on its page.
  await openProperty(page, "Aspen Row");
  await expect(page.getByTestId("nsf-fee")).toHaveText("No NSF fee");
  const nsf = await setupPanel(page, "Set NSF fee");
  await nsf.getByLabel("NSF fee from").fill(firstOfThisMonth());
  await nsf.getByLabel("NSF fee", { exact: true }).fill("35.00");
  await nsf.getByRole("button", { name: "Set NSF fee" }).click();
  await expect(page.getByTestId("nsf-fee")).toHaveText("NSF fee $35.00");

  const lease = await openLease(page, "Aspen Row, Unit 1");
  const owed = lease.getByTestId("balance-due");
  const prepaid = lease.getByTestId("prepaid");
  await expect(owed).toHaveText("$0.00");

  // A one-off fee, then a credit that comes off it.
  let charge = await leasePanel(page, "Enter charge");
  await charge.getByLabel("Add a").selectOption("fee");
  await charge.getByLabel("Amount", { exact: true }).fill("25.00");
  await charge.getByLabel("What for").fill("Key replacement");
  await charge.getByRole("button", { name: "Add", exact: true }).click();
  await expect(lease).toContainText("Fee of $25.00 added.");
  await expect(owed).toHaveText("$25.00");
  charge = await leasePanel(page, "Enter charge");
  await charge.getByLabel("Add a").selectOption("credit");
  await charge.getByLabel("Amount", { exact: true }).fill("10.00");
  await charge.getByLabel("What for").fill("Goodwill");
  await charge.getByRole("button", { name: "Add", exact: true }).click();
  await expect(lease).toContainText("Credit of $10.00 added.");
  await expect(owed).toHaveText("$15.00");

  // A payment over what is owed: the rest is the tenant's prepaid rent, used on the next fee.
  let pay = await leasePanel(page, "Receive payment");
  await pay.getByLabel("Payment received").fill("100.00");
  await pay.getByLabel("Check or reference").fill("Check 501");
  await pay.getByRole("button", { name: "Record payment" }).click();
  await expect(lease).toContainText("$15.00 paid what is owed; $85.00 held as prepaid rent.");
  await expect(owed).toHaveText("$0.00");
  await expect(prepaid).toHaveText("$85.00");
  charge = await leasePanel(page, "Enter charge");
  await charge.getByLabel("Add a").selectOption("fee");
  await charge.getByLabel("Amount", { exact: true }).fill("30.00");
  await charge.getByLabel("What for").fill("Lock change");
  await charge.getByRole("button", { name: "Add", exact: true }).click();
  await expect(owed).toHaveText("$30.00");
  await (await leasePanel(page, "Receive payment")).getByRole("button", { name: "Use prepaid rent" }).click();
  await expect(lease).toContainText("$30.00 paid what is owed.");
  await expect(owed).toHaveText("$0.00");
  await expect(prepaid).toHaveText("$55.00");

  // The ledger: each charge, payment and credit with the balance after it.
  await leaseTab(page, "Ledger");
  const ledger = lease.getByRole("table", { name: "Ledger" });
  await expect(ledger).toContainText("Key replacement");
  await expect(ledger.getByRole("row", { name: /Credit/ })).toContainText("($10.00)");
  await expect(ledger.getByRole("row", { name: /Check 501/ })).toContainText("$15.00");
  // Each payment is listed as it came in, not only the part that paid charges.
  const received = lease.getByRole("table", { name: "Payments received" });
  await expect(received.getByRole("row", { name: /Check 501/ })).toContainText("$100.00");

  // A payment when nothing is owed is all prepaid rent; it is listed with how it came (a money
  // order here), and it can bounce.
  pay = await leasePanel(page, "Receive payment");
  await pay.getByLabel("Payment received").fill("50.00");
  await pay.getByLabel("Method").selectOption({ label: "Money order" });
  await pay.getByLabel("Check or reference").fill("MO 503");
  await pay.getByRole("button", { name: "Record payment" }).click();
  await expect(lease).toContainText("$50.00 held as prepaid rent.");
  const prepaidOnly = received.getByRole("row", { name: /MO 503/ });
  await expect(prepaidOnly).toContainText("Money order · MO 503");
  await expect(prepaidOnly).toContainText("$50.00");
  await prepaidOnly.getByRole("button", { name: /^Actions for / }).click();
  await page.getByRole("menuitem", { name: "It bounced" }).click();
  await prepaidOnly.getByRole("button", { name: "Record the $50.00 bounce" }).click();
  await expect(lease).toContainText("The $50.00 payment bounced and is undone; a $35.00 NSF fee is charged.");
  await expect(owed).toHaveText("$35.00");
  await (await leasePanel(page, "Receive payment")).getByRole("button", { name: "Use prepaid rent" }).click();
  await expect(lease).toContainText("$35.00 paid what is owed.");
  await expect(owed).toHaveText("$0.00");
  await leaseTab(page, "Summary");
  await expect(prepaid).toHaveText("$20.00");

  // A monthly charge from next month, then stopped from its ⋯ menu.
  const nextMonth = new Date(Date.UTC(Number(today().slice(0, 4)), Number(today().slice(5, 7)), 1))
    .toISOString()
    .slice(0, 10);
  const monthly = await leasePanel(page, "Add recurring charge");
  await monthly.getByLabel("Every month, a").selectOption("fee");
  await monthly.getByLabel("Monthly amount").fill("40.00");
  await monthly.getByLabel("First due on").fill(nextMonth);
  await monthly.getByLabel("Monthly, for").fill("Parking");
  await monthly.getByRole("button", { name: "Add every month" }).click();
  await expect(lease).toContainText("Parking of $40.00 added every month.");
  await expect(lease).toContainText(`Parking: $40.00 a month from ${us(nextMonth)}`);
  await lease.getByRole("button", { name: "Actions for Parking" }).click();
  await page.getByRole("menuitem", { name: "Stop" }).click();
  await lease.getByLabel("Stops on, Parking").fill(nextMonth);
  await lease.getByRole("button", { name: "Stop it" }).click();
  await expect(lease).toContainText(`Parking stops ${us(nextMonth)}.`);
  await expect(lease).toContainText(`Parking: $40.00 a month from ${us(nextMonth)}, stops ${us(nextMonth)}`);

  // A deposit held for the tenant, then a pet deposit held apart from it.
  let deposit = await leasePanel(page, "Record deposit");
  await deposit.getByLabel("Deposit received", { exact: true }).fill("100.00");
  await deposit.getByRole("button", { name: "Record deposit" }).click();
  await expect(lease).toContainText("Security deposit of $100.00 held.");
  await expect(lease.getByTestId("deposit-held")).toHaveText("$1,280.00");
  await expect(lease.getByTestId("deposit-kinds")).toHaveCount(0);
  deposit = await leasePanel(page, "Record deposit");
  await deposit.getByLabel("Kind of deposit").selectOption("pet");
  await deposit.getByLabel("Deposit received", { exact: true }).fill("250.00");
  await deposit.getByRole("button", { name: "Record deposit" }).click();
  await expect(lease).toContainText("Pet deposit of $250.00 held.");
  await expect(lease.getByTestId("deposit-held")).toHaveText("$1,530.00");
  await expect(lease.getByTestId("deposit-kinds")).toHaveText("Security deposit $1,280.00 · Pet deposit $250.00");

  // A payment that bounces is owed again, with the property's NSF fee.
  charge = await leasePanel(page, "Enter charge");
  await charge.getByLabel("Add a").selectOption("fee");
  await charge.getByLabel("Amount", { exact: true }).fill("20.00");
  await charge.getByLabel("What for").fill("Late key return");
  await charge.getByRole("button", { name: "Add", exact: true }).click();
  pay = await leasePanel(page, "Receive payment");
  await pay.getByLabel("Payment received").fill("20.00");
  await pay.getByLabel("Check or reference").fill("Check 502");
  await pay.getByRole("button", { name: "Record payment" }).click();
  await expect(lease).toContainText("$20.00 paid what is owed.");
  await leaseTab(page, "Ledger");
  const payment = received.getByRole("row", { name: /Check 502/ });
  await payment.getByRole("button", { name: /^Actions for / }).click();
  await page.getByRole("menuitem", { name: "It bounced" }).click();
  await payment.getByRole("button", { name: "Record the $20.00 bounce" }).click();
  await expect(lease).toContainText("The $20.00 payment bounced and is undone; a $35.00 NSF fee is charged.");
  await expect(owed).toHaveText("$55.00");
  await expect(received.getByRole("row", { name: /Check 502/ }).getByText("Bounced")).toBeVisible();
  await expect(ledger.getByRole("row", { name: /NSF fee/ }).last()).toContainText("$35.00");
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
  // Over Demo Owner A's $500.00 approval limit.
  await recordBill(page, "Demo Plumbing Co", "Demo Owner A, Birch Flats", "PLB-2001", "800.00", "Water heater");
  await recordBill(page, "Demo Plumbing Co", "Demo Owner A, Birch Flats", "PLB-2002", "650.00", "Shower valve");
  const bill = (reference: string) => billRow(page, "Demo Plumbing Co", reference);
  await expect(bill("PLB-2001").getByTestId("status")).toHaveText("Awaiting owner approval");
  // The bill's own page says the owner was emailed.
  await bill("PLB-2001").getByRole("cell").first().click();
  await expect(page.getByRole("heading", { level: 1, name: "Demo Plumbing Co PLB-2001" })).toBeVisible();
  await expect(page.getByRole("main")).toContainText("Emailed the owner to approve or decline it in their portal.");
  await expect(page.getByTestId("bill-email")).toContainText("sent.");

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

  // Staff see both decisions: the declined bill is closed, the approved one is to pay.
  await section(page, "Bills");
  await expect(bill("PLB-2002").getByTestId("status")).toHaveText("Approved");
  await expect(bill("PLB-2001")).toHaveCount(0); // nothing left to pay on it
  // Birch Flats holds only its $250.00 reserve: setting $650.00 aside is refused, in words.
  const aside = await billStep(page, "Demo Plumbing Co", "PLB-2002", "Set aside");
  await aside.getByRole("button", { name: "Set aside" }).click();
  await expect(aside.getByRole("alert")).toHaveText("not enough money: the owner's account would be $400.00 short");
  await aside.getByRole("button", { name: "Close" }).click();
  // The owner sends the money; it covers the approved bill, so nothing more is available to draw.
  await recordContribution(page, "Demo Owner A", "Birch Flats", "650.00", "Shower valve");
  const birch = await ownerProperty(page, "Demo Owner A", "Birch Flats");
  await expect(birch.getByRole("cell").last()).toHaveText("$0.00");

  // Pay bills sets it aside and pays it at once, showing Birch Flats' cash first.
  await section(page, "Bills");
  await page.getByRole("link", { name: "Pay bills" }).click();
  await page.getByLabel("Pay Demo Plumbing Co PLB-2002").check();
  const cash = page.getByRole("table", { name: "Each property's cash" }).getByRole("row", { name: "Demo Owner A, Birch Flats" });
  await expect(cash.getByRole("cell")).toHaveText(["Demo Owner A, Birch Flats", "$900.00", "$650.00", "$250.00", ""]);
  await page.getByRole("combobox", { name: "Sent by" }).selectOption({ label: "EFT" });
  await page.getByRole("button", { name: "Pay $650.00" }).click();
  await page.getByRole("alertdialog").getByRole("button", { name: "Confirm" }).click();
  await expect(page.getByText("1 bill of $650.00 paid, by EFT.")).toBeVisible();
  await expect(page.getByText("No bill is ready to pay.")).toBeVisible();

  await section(page, "Bills");
  await page.getByRole("tab", { name: "Paid", exact: true }).click();
  await expect(bill("PLB-2002").getByTestId("status")).toHaveText("Paid");
  await bill("PLB-2002").getByRole("cell").first().click();
  await expect(page.getByRole("list", { name: "History" })).toContainText("Paid by EFT");
  await section(page, "Bills");
  await page.getByRole("tab", { name: "Declined", exact: true }).click();
  await expect(bill("PLB-2001").getByTestId("status")).toHaveText("Declined");
  await bill("PLB-2001").getByRole("cell").first().click();
  await expect(page.getByRole("main")).toContainText("Get a second quote");
});

test("staff add a recurring bill and stop it, and record the decline of an owner with no login", async ({
  page,
}) => {
  const people = seed.features["recurring-bills"]!;
  await joinAsStaff(page, people.bookkeeper);
  await section(page, "Bills");
  await page.getByRole("tab", { name: "Recurring" }).click();
  await expect(page.getByText("No recurring bills yet.")).toBeVisible();
  await page.getByRole("button", { name: "Add recurring bill" }).click();
  const adding = page.getByRole("dialog", { name: "Add recurring bill" });
  await adding.getByRole("combobox", { name: "Vendor" }).selectOption({ label: "Demo Roofing Co" });
  await adding.getByRole("combobox", { name: "Owner and property" }).selectOption({ label: "Demo Owner B, Aspen Row" });
  await adding.getByLabel("Ref # prefix").fill("GUTTER");
  await adding.getByLabel("Monthly amount").fill("120.00");
  await adding.getByLabel("Memo").fill("Gutter cleaning");
  await adding.getByLabel("Day of the month").fill("1");
  await adding.getByLabel("First day").fill(firstOfThisMonth());
  await adding.getByRole("button", { name: "Add recurring bill" }).click();
  await expect(page.getByText("GUTTER is entered each month.")).toBeVisible();
  // Each month's bill is entered by the nightly run (src/pms/nightly.py), off in this run.
  const gutter = page.getByRole("row", { name: "Demo Roofing Co GUTTER" });
  await expect(gutter.getByRole("cell").nth(3)).toHaveText("$120.00");

  await gutter.getByRole("button", { name: "Actions for Demo Roofing Co GUTTER" }).click();
  await page.getByRole("menuitem", { name: "Stop it" }).click();
  const stopping = page.getByRole("dialog", { name: "Stop recurring bill" });
  await stopping.getByLabel("Last day").fill(today());
  await stopping.getByRole("button", { name: "Stop it" }).click();
  await expect(page.getByText(`GUTTER stops after ${us(today())}.`)).toBeVisible();
  await expect(gutter.getByRole("cell").nth(6)).toHaveText(us(today()));

  // A bill over the limit of an owner with no portal login: no email can go, so staff record
  // the owner's answer themselves, here a decline.
  await recordBill(page, "Demo Roofing Co", "Demo Owner A, Elm Court", "RF-3001", "900.00", "Skylight");
  const skylight = billRow(page, "Demo Roofing Co", "RF-3001");
  // It is due today, so the Dashboard lists it.
  await section(page, "Dashboard");
  const due = page.getByRole("region", { name: "Bills due this week" });
  await expect(due).toContainText("RF-3001");
  await due.getByRole("link", { name: "Open Bills" }).click();
  await skylight.getByRole("cell").first().click();
  await expect(page.getByRole("main")).toContainText(
    "No email went to the owner: they have no active portal login. Ask them yourself.",
  );
  await page.getByRole("button", { name: "Record decline" }).click();
  const declining = page.getByRole("dialog", { name: "Record decline" });
  await declining.getByLabel("Declined by").fill("Demo Owner A, by phone");
  await declining.getByLabel("Why").fill("Not this year");
  await declining.getByRole("button", { name: "Record decline" }).click();
  await expect(page.getByText("The owner's decline of RF-3001 is recorded.")).toBeVisible();
  await expect(page.getByTestId("status")).toHaveText("Declined");
  await expect(page.getByRole("main")).toContainText("Declined by Demo Owner A, by phone: Not this year.");
});

test("staff take the month's management fees once, find no leasing fee due, and pay owners only what is available", async ({
  page,
}) => {
  const people = seed.features.owners!;
  await joinAsStaff(page, people.manager);
  const birch = await ownerProperty(page, "Demo Owner A", "Birch Flats");
  await expect(birch.getByRole("cell").nth(2)).toHaveText("$250.00");
  await expect(birch.getByRole("cell").nth(3)).toHaveText("$250.00");

  // Management fees: nothing was collected yet this month, so Demo Owner A's agreements would
  // take their $50.00 minimum; looking takes nothing, and Collect fees takes it, once.
  await section(page, "Management fees");
  const nextMonth = new Date(Date.UTC(Number(today().slice(0, 4)), Number(today().slice(5, 7)), 1))
    .toISOString()
    .slice(0, 10);
  await page.getByLabel("Period from").fill(firstOfThisMonth());
  await page.getByLabel("Up to (not including)").fill(nextMonth);
  const fee = page.getByRole("row", { name: "Demo Owner A, Birch Flats", exact: true });
  await expect(fee).toContainText("$50.00");
  await expect(fee).toContainText("To collect");
  await page.getByRole("button", { name: "Collect fees" }).click();
  await page.getByRole("alertdialog").getByRole("button", { name: "Confirm" }).click();
  await expect(fee).toContainText("Collected");
  await expect(page.getByRole("button", { name: "Collect fees" })).toBeDisabled();
  await expect((await ownerProperty(page, "Demo Owner A", "Birch Flats")).getByRole("cell").nth(2)).toHaveText("$200.00");

  // The demo's agreements take no leasing fee, and the lease's ⋯ menu says so.
  await openLease(page, "Aspen Row, Unit 1");
  const leasing = await leasePanel(page, "Take leasing fee");
  await leasing.getByLabel("From owner").selectOption({ label: "Demo Owner B" });
  await leasing.getByRole("button", { name: "Take leasing fee" }).click();
  await expect(page.getByRole("main")).toContainText(
    "No leasing fee: the owner's agreement in force takes none, so none is due for this lease.",
  );

  // Aspen Row holds only its reserve: it can't be chosen on Pay owners.
  await section(page, "Pay owners");
  const aspen = page.getByRole("row", { name: "Demo Owner B, Aspen Row", exact: true });
  await expect(aspen.getByLabel("Pay Demo Owner B, Aspen Row")).toBeDisabled();
  // The owner sends money in, and it is on their statement, to read and to download.
  await recordContribution(page, "Demo Owner B", "Aspen Row", "300.00", "Roof fund");
  await expect(page.getByRole("main")).toContainText("Roof fund");
  const pdf = await page.request.get((await page.getByRole("link", { name: "PDF" }).getAttribute("href")) ?? "");
  expect(pdf.status()).toBe(200);
  // More than is available is refused, in words, and pays nothing.
  await payOwner(page, "Demo Owner B", "Aspen Row", "350.00");
  await expect(page.getByRole("alert")).toHaveText(
    "Demo Owner B, Aspen Row: only $300.00 is available to draw (the balance less the $200.00 reserve);"
      + " $350.00 is more than that",
  );
  await expect(aspen.getByRole("cell").nth(6)).toHaveText("$300.00");
  // Part of it by EFT, then the rest.
  await aspen.getByLabel("Amount to pay Demo Owner B, Aspen Row").fill("100.00");
  await page.getByLabel("Sent by").selectOption("EFT");
  await page.getByRole("button", { name: "Pay $100.00" }).click();
  await page.getByRole("alertdialog").getByRole("button", { name: "Confirm" }).click();
  await expect(page.getByRole("main")).toContainText("1 payout of $100.00 recorded, by EFT.");
  await expect(aspen.getByRole("cell").nth(6)).toHaveText("$200.00");
  // How they were sent stays chosen for the next payout.
  await payOwner(page, "Demo Owner B", "Aspen Row");
  await expect(page.getByRole("main")).toContainText("1 payout of $200.00 recorded, by EFT.");
  await expect(aspen.getByRole("cell").nth(6)).toHaveText("$0.00");

  // Several owners at once: money comes in for Aspen Row and Elm Court, the box at the top chooses
  // every owner with money available, and Pay records both payouts by check, all or none.
  await recordContribution(page, "Demo Owner B", "Aspen Row", "150.00");
  await recordContribution(page, "Demo Owner A", "Elm Court", "400.00");
  await section(page, "Pay owners");
  await page.getByLabel("Choose every owner with money available").check();
  const choice = (name: string) => page.getByRole("checkbox", { name: `Pay ${name}`, exact: true });
  await expect(choice("Demo Owner B, Aspen Row")).toBeChecked();
  await expect(choice("Demo Owner A, Elm Court")).toBeChecked();
  await expect(choice("Demo Owner A, Birch Flats")).not.toBeChecked();
  // Each pays everything available unless an amount is typed: Elm Court pays $300.00 of it.
  const elm = page.getByRole("row", { name: "Demo Owner A, Elm Court", exact: true });
  const elmAvailable = Number(((await elm.getByRole("cell").nth(6).textContent()) ?? "").replace(/[$,]/g, ""));
  await elm.getByLabel("Amount to pay Demo Owner A, Elm Court").fill("300.00");
  await page.getByLabel("Sent by").selectOption({ label: "Check" });
  await page.getByRole("button", { name: "Pay $450.00" }).click();
  await page.getByRole("alertdialog").getByRole("button", { name: "Confirm" }).click();
  await expect(page.getByRole("main")).toContainText("2 payouts of $450.00 recorded, by check.");
  await expect(aspen.getByRole("cell").nth(6)).toHaveText("$0.00");
  await expect(elm.getByRole("cell").nth(6)).toHaveText(DOLLARS.format(elmAvailable - 300));
});

test("only staff holding a role do its work: an admin grants pay-out and adds staff, staff sign in again with their code, and a disabled login is signed out for good", async ({
  browser,
  page,
}) => {
  const people = seed.features.roles!;
  const secret = await joinAsStaff(page, people.bookkeeper);
  await section(page, "Bills");
  await recordBill(page, "Demo Plumbing Co", "Demo Owner B, Aspen Row", "PLB-3001", "100.00", "Faucet washer");
  const bill = billRow(page, "Demo Plumbing Co", "PLB-3001");
  await (await billStep(page, "Demo Plumbing Co", "PLB-3001", "Set aside")).getByRole("button", { name: "Set aside" }).click();
  await expect(bill.getByTestId("status")).toHaveText("Set aside");
  // The bookkeeper holds no role: paying out is refused.
  const paying = await billStep(page, "Demo Plumbing Co", "PLB-3001", "Pay");
  await paying.getByRole("button", { name: "Pay $100.00" }).click();
  await page.getByRole("alertdialog").getByRole("button", { name: "Confirm" }).click();
  await expect(paying.getByRole("alert")).toHaveText("Demo Bookkeeper may not pay out money (the disburse role)");
  await paying.getByRole("button", { name: "Close" }).click();

  // The manager, an admin, grants it; and can't leave the company without an admin.
  const admin = await (await browser.newContext()).newPage();
  await joinAsStaff(admin, people.manager);
  await settings(admin, "Staff and roles");
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
  await expect(admin.getByText("Added Demo Leasing Agent; invite them from their row.")).toBeVisible();
  await expect(admin.getByRole("checkbox", { name: "Demo Leasing Agent: Approve" })).not.toBeChecked();
  // Invited from their row: the link is shown once, to send them.
  const agent = admin.getByRole("row").filter({ has: admin.getByRole("checkbox", { name: "Demo Leasing Agent: Admin" }) });
  await expect(agent).toContainText("No login");
  await agent.getByRole("button", { name: "Invite" }).click();
  const invite = admin.getByRole("dialog", { name: "Invite Demo Leasing Agent" });
  await invite.getByLabel("Email to sign in with").fill("leasing.agent@example.com");
  await invite.getByRole("button", { name: "Invite to sign in" }).click();
  await expect(invite.getByLabel("Invite link, Demo Leasing Agent")).toHaveValue(/\/invite#/);
  await invite.getByRole("button", { name: "Close" }).click();
  await expect(agent).toContainText("Invited");

  // Now the bookkeeper pays it, from its own page.
  await page.reload();
  await bill.getByRole("cell").first().click();
  await page.getByRole("button", { name: "Pay", exact: true }).click();
  await page.getByRole("dialog", { name: "Pay" }).getByRole("button", { name: "Pay $100.00" }).click();
  await page.getByRole("alertdialog").getByRole("button", { name: "Confirm" }).click();
  await expect(page.getByText("PLB-3001 is paid.")).toBeVisible();
  await expect(page.getByTestId("status")).toHaveText("Paid");

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
  await settings(admin, "Logins");
  const bookkeeper = admin.getByRole("row", { name: "Demo Bookkeeper", exact: true });
  await chooseAction(admin, "Demo Bookkeeper", "Disable");
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
  // Each rent receipt names its tenants and home (its memo, src/pms/api/rent.py).
  await expect(birch).toContainText("Rent – Morgan Demo, Birch Flats Unit A · Demo operating trust account");
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
  await expect(page.getByText("Quinn Demo, Taylor Demo", { exact: true })).toBeVisible();
  // Last month's rent is paid and this month's not charged yet: nothing is due.
  await expect(page.getByTestId("balance-due")).toHaveText("$0.00");
  await expect(part(page, "Coming up")).toContainText("Rent: $2,250.00");

  const ledger = page.getByRole("table").filter({ hasText: "Charged" });
  await expect(ledger.getByRole("row", { name: /^\d{2}\/\d{2}\/\d{4} Rent due/ })).toHaveCount(2);
  await expect(ledger.getByRole("row", { name: /^\d{2}\/\d{2}\/\d{4} Payment/ })).toHaveCount(2);
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
  // This month's rent, with no late fees yet: an admin's Run rent now, as the nightly run does.
  await section(page, "Rent roll");
  await page.getByRole("button", { name: "Actions for the rent roll" }).click();
  await page.getByRole("menuitem", { name: "Run rent now" }).click();
  const run = page.getByRole("dialog", { name: "Run rent now" });
  await run.getByLabel("Late fees as of").fill(firstOfThisMonth());
  await run.getByRole("button", { name: "Run rent now" }).click();
  await expect(page.getByRole("main")).toContainText("6 rents, 1 recurring charge and 0 late fees charged.");

  // Drew Sample's last day was yesterday: this month's $1,180.00 comes down to the days before.
  const yesterday = new Date(Date.now() - 86_400_000).toISOString().slice(0, 10);
  const [year, month] = today().split("-").map(Number) as [number, number];
  const daysInMonth = new Date(Date.UTC(year, month, 0)).getUTCDate();
  const daysInEffect = yesterday.slice(0, 7) === today().slice(0, 7) ? Number(yesterday.slice(8, 10)) : 0;
  const cents = Math.round((118_000 * daysInEffect) / daysInMonth);
  const dollars = (c: number) =>
    `$${Math.floor(c / 100).toLocaleString("en-US")}.${String(c % 100).padStart(2, "0")}`;

  await section(page, "Move-outs");
  const lease = page.getByRole("region", { name: "Aspen Row / Unit 1: Drew Sample" });
  await expect(lease).toContainText("owes $1,180.00");
  await lease.getByText("Move-out for Unit 1").click();
  await lease.getByLabel("Last day").fill(yesterday);
  await lease.getByRole("button", { name: "End the lease" }).click();
  await expect(lease).toContainText(`The lease ends on ${us(yesterday)}.`);
  await expect(lease.getByText("Ended, deposit to return")).toBeVisible();
  await expect(lease).toContainText(`owes ${dollars(cents)}`);

  // Cleaning is charged; what is owed comes out of the deposit, the rest goes back.
  await lease.getByLabel("Cleaning or damage").fill("Carpet cleaning");
  await lease.getByLabel("Amount").fill("100.00");
  await lease.getByRole("button", { name: "Charge it" }).click();
  await expect(lease).toContainText("Charged $100.00: Carpet cleaning.");
  // Owing something, the deposit is due back 14 days after the last day (the strictest deadline
  // found), here and on the Dashboard; the fee keeps its receipt.
  const returnBy = us(daysFromToday(13));
  await expect(lease).toContainText(`Return by ${returnBy}`);
  await lease.getByLabel("Receipt for Carpet cleaning").setInputFiles({
    name: "carpet-cleaning.pdf", mimeType: PDF, buffer: Buffer.from("%PDF-1.4\n% synthetic receipt\n"),
  });
  await lease.getByRole("button", { name: "Add receipt" }).click();
  await expect(page.getByText("Kept carpet-cleaning as a receipt for Carpet cleaning.")).toBeVisible();
  await expect(lease.getByRole("link", { name: "carpet-cleaning" })).toBeVisible();
  await section(page, "Dashboard");
  await expect(page.getByRole("region", { name: "Deposits to return" })).toContainText(
    `Unit 1, Aspen Row (Drew Sample): $1,180.00 held, due back by ${returnBy}`,
  );
  await section(page, "Move-outs");
  const kept = cents + 10_000;
  const back = 118_000 - kept;
  await expect(lease).toContainText(`Keeps ${dollars(kept)} for what the lease owes; ${dollars(back)} goes back.`);
  await lease.getByRole("button", { name: "Return the deposit" }).click();
  // Money leaving trust waits for staff to confirm the amounts.
  await expect(page.getByRole("alertdialog")).toContainText(
    `keep ${dollars(kept)} for what the lease owes and pay ${dollars(back)} back to the tenants?`,
  );
  await page.getByRole("alertdialog").getByRole("button", { name: "Confirm" }).click();
  await expect(lease).toContainText(`Returned ${dollars(back)} to the tenants; the rest paid what the lease owed.`);
  await expect(lease.getByText("Deposit returned")).toBeVisible();
  await downloads(page, "Statement for Drew Sample (PDF)", PDF);

  // The tenant downloads the same statement from the portal; with their login, staff email it too.
  const tenant = await (await browser.newContext()).newPage();
  await joinPortal(tenant, people.drew);
  await downloads(tenant, "Deposit statement (PDF)", PDF);
  await lease.getByRole("button", { name: "Email the statement" }).click();
  await expect(page.getByText(`Emailed Drew Sample at ${people.emails.drew}.`)).toBeVisible();
});

test("staff list a vacant unit on the public page; someone applies from it, staff screen and approve it and record its withdrawal, take it off the page and close the link", async ({
  browser,
  page,
}) => {
  const people = seed.features.listings!;
  await joinAsStaff(page, people.manager);
  await section(page, "Listings");
  // A link to post anywhere first, shown once; then the unit goes on the public page with it.
  const unit = page.getByRole("row", { name: "Aspen Row, Unit 2" });
  await expect(unit.getByTestId("listed")).toHaveText("Not listed");
  await unit.getByRole("button", { name: "Actions for Aspen Row, Unit 2" }).click();
  await page.getByRole("menuitem", { name: "Open an application link" }).click();
  const linking = page.getByRole("dialog", { name: "Open an application link" });
  await linking.getByLabel("Rent a month").fill("1240.00");
  await linking.getByRole("button", { name: "Open the link" }).click();
  await expect(linking.getByTestId("application-link")).toContainText("/apply#");
  const opened = new URL((await linking.getByTestId("application-link").textContent()) ?? "");
  await linking.getByRole("button", { name: "Close" }).click();
  await unit.getByRole("button", { name: "List", exact: true }).click();
  const listing = page.getByRole("dialog", { name: "List on the public page" });
  await expect(listing).toContainText("At $1,240.00 a month, as its open link asks.");
  await listing.getByLabel("Description (optional)").fill("Two bedrooms, second floor.");
  await listing.getByRole("button", { name: "List it" }).click();
  await expect(page.getByText("Listed Aspen Row, Unit 2 at $1,240.00.")).toBeVisible();
  await expect(unit.getByTestId("listed")).toHaveText("Listed");
  await page.getByRole("button", { name: "Copy public page link" }).click();
  await expect(page.getByText(/public page's link/)).toBeVisible();

  // Anyone, signed in or not, sees it on the PMC's vacancy page and applies from there.
  const visitor = await (await browser.newContext()).newPage();
  await visitor.goto(`/listings?pmc=${people.pmc_id}`);
  const shown = visitor.getByRole("listitem", { name: "Aspen Row, Unit 2" });
  await expect(shown).toContainText("$1,240.00 a month");
  await expect(shown).toContainText("Two bedrooms, second floor.");
  await shown.getByRole("link", { name: "Apply" }).click();
  await expect(visitor.getByRole("heading", { name: "Apply for Aspen Row, Unit 2" })).toBeVisible();
  await visitor.getByLabel("Move-in day").fill(new Date(Date.now() + 20 * 86_400_000).toISOString().slice(0, 10));
  await visitor.getByLabel("Monthly income of everyone applying, together").fill("5200");
  await visitor.getByLabel("Full name").fill("Applicant Listing");
  await visitor.getByLabel("Email").fill("applicant.listing@example.com");
  await visitor.getByRole("button", { name: "Send application" }).click();
  const statusUrl = new URL((await visitor.getByTestId("status-url").textContent()) ?? "");
  await page.reload();
  await expect(page.getByRole("row", { name: "Aspen Row, Unit 2" }).getByRole("cell").nth(3)).toHaveText("1");

  // It waits for a decision on the Dashboard; on Applicants staff open it, screen and approve it.
  await section(page, "Dashboard");
  await expect(page.getByRole("region", { name: "Applications waiting" })).toContainText("Applicant Listing");
  await section(page, "Applicants");
  const row = page.getByRole("row", { name: "Application from Applicant Listing" });
  await expect(row.getByTestId("status")).toHaveText("New");
  await expect(row.getByRole("cell").last()).toHaveText("4.1x");
  await row.click();
  await expect(page.getByRole("heading", { level: 1, name: "Applicant Listing" })).toBeVisible();
  await page.getByRole("button", { name: "Screen" }).click();
  await expect(page.getByRole("list", { name: "Screenings" })).toContainText("Recommends approval");
  await expect(page.getByTestId("status")).toHaveText("Screening");
  await page.getByRole("button", { name: "Approve" }).click();
  const deciding = page.getByRole("dialog", { name: "Approve" });
  await deciding.getByRole("button", { name: "Approve" }).click();
  await expect(page.getByText("Approved: Applicant Listing.")).toBeVisible();
  await expect(page.getByTestId("status")).toHaveText("Approved");
  await expect(page.getByRole("link", { name: "Create draft lease" })).toHaveAttribute("href", /\/draft-leases\?application=/);
  await visitor.goto(statusUrl.pathname + statusUrl.hash);
  await expect(visitor.getByTestId("application-status")).toContainText("Approved");

  // The applicant changes their mind: staff record it from the ⋯ menu, and it takes nothing more.
  await page.getByRole("button", { name: "Actions for Applicant Listing" }).click();
  await page.getByRole("menuitem", { name: "Record withdrawal" }).click();
  const withdrawing = page.getByRole("dialog", { name: "Record withdrawal" });
  await withdrawing.getByLabel("Why (optional)").fill("Found another home");
  await withdrawing.getByRole("button", { name: "Record withdrawal" }).click();
  await expect(page.getByText("Withdrawn: Applicant Listing.")).toBeVisible();
  await expect(page.getByTestId("status")).toHaveText("Withdrawn");
  await expect(page.getByTestId("withdrawal")).toContainText("Found another home");
  await expect(page.getByRole("link", { name: "Create draft lease" })).toHaveCount(0);
  await visitor.reload(); // the status page is open already
  await expect(visitor.getByTestId("application-status")).toHaveText("Withdrawn.");
  await section(page, "Applicants");
  await expect(page.getByText("No application is waiting.")).toBeVisible();
  await page.getByRole("tab", { name: "Withdrawn", exact: true }).click();
  await expect(page.getByRole("row", { name: "Application from Applicant Listing" }).getByTestId("status")).toHaveText("Withdrawn");

  // Unlisted, it is gone from the public page.
  await section(page, "Listings");
  const again = page.getByRole("row", { name: "Aspen Row, Unit 2" });
  await again.getByRole("button", { name: "Unlist" }).click();
  await expect(page.getByText("Took Aspen Row, Unit 2 off the public page.")).toBeVisible();
  await expect(again.getByTestId("listed")).toHaveText("Not listed");
  await visitor.goto(`/listings?pmc=${people.pmc_id}`);
  await expect(visitor.getByText("Nothing is available right now.")).toBeVisible();

  // Closed, its link takes no more applications.
  await again.getByRole("button", { name: "Actions for Aspen Row, Unit 2" }).click();
  await page.getByRole("menuitem", { name: "Close its link" }).click();
  await page.getByRole("alertdialog").getByRole("button", { name: "Close its link" }).click();
  await expect(page.getByText("Closed the link for Aspen Row, Unit 2.")).toBeVisible();
  await expect(again.getByRole("cell").nth(3)).toHaveText("0");
  await visitor.goto(opened.pathname + opened.hash);
  await expect(visitor.getByRole("alert")).toHaveText("this application link is closed or doesn't exist");
});

test("staff set management terms with a leasing fee, late fees, a bank account and a vendor, and a new lease takes its leasing fee", async ({
  page,
}) => {
  const people = seed.features.setup!;
  await joinAsStaff(page, people.manager);

  // A trust bank account added from Banking opens its own page; a vendor added from Vendors, theirs.
  await section(page, "Banking");
  await page.getByRole("button", { name: "Add bank account" }).click();
  const adding = page.getByRole("dialog", { name: "Add bank account" });
  await adding.getByLabel("Holds").selectOption({ label: "Operating: rent and owners' money" });
  await adding.getByLabel("Bank account name").fill("Second operating account");
  await adding.getByRole("button", { name: "Add bank account" }).click();
  await expect(page.getByRole("heading", { level: 1, name: "Second operating account" })).toBeVisible();
  await section(page, "Vendors");
  const vendor = await setupPanel(page, "Add vendor");
  await vendor.getByLabel("Vendor's name").fill("Demo Electric Co");
  await vendor.getByRole("button", { name: "Add vendor" }).click();
  await expect(page.getByRole("heading", { level: 1, name: "Demo Electric Co" })).toBeVisible();

  // New terms for Aspen Row from today, from its page: a 50% leasing fee on leases starting from now.
  await openProperty(page, "Aspen Row");
  const owners = page.getByRole("table", { name: "Owners" });
  const terms = await setupPanel(page, "Set terms", "Set management terms", owners.getByRole("row", { name: "Demo Owner B" }));
  await expect(terms.getByRole("combobox", { name: "For" })).toHaveValue(/.+/);
  await terms.getByLabel("In force from").fill(today());
  await terms.getByLabel("Fee, percent of rent and recurring fees collected").fill("10");
  await terms.getByLabel("Leasing fee, percent of rent").fill("50");
  await terms.getByLabel("Reserve").fill("200.00");
  await terms.getByLabel("Owner approves bills over").fill("300.00");
  await terms.getByRole("button", { name: "Set terms" }).click();
  await expect(owners.getByRole("row", { name: "Demo Owner B" })).toContainText(`from ${us(today())}`);

  // Late fees for Aspen Row: three days' grace, then $25.00.
  const late = await setupPanel(page, "Set late fees");
  await late.getByLabel("Late fees from").fill(today());
  await late.getByLabel("Grace days").fill("3");
  await late.getByLabel("Flat fee").fill("25.00");
  await late.getByRole("button", { name: "Set late fees" }).click();
  await expect(page.getByTestId("late-fees")).toContainText("After 3 days' grace: $25.00");

  // A new tenant on the vacant unit, from today, from the unit's page.
  await page.getByRole("row", { name: "Unit 2" }).getByRole("link", { name: "Unit 2" }).click();
  const lease = await setupPanel(page, "Add lease in place", "Add lease");
  await lease.getByLabel("Tenant's name").fill("Avery Sample");
  await lease.getByRole("button", { name: "Add tenant" }).click();
  await expect(lease.getByLabel("Avery Sample")).toBeChecked();
  await lease.getByLabel("Starts").fill(today());
  await lease.getByLabel("Rent a month").fill("1240.00");
  await lease.getByRole("button", { name: "Add lease" }).click();
  await expect(page.getByRole("row", { name: "Avery Sample" })).toContainText(`${us(today())} – Month to month`);
  await expect(page.getByRole("row", { name: "Avery Sample" })).toContainText("$1,240.00");

  // Its leasing fee is half a month's rent, once the owner has the money for it, taken from
  // the lease's ⋯ menu.
  await recordContribution(page, "Demo Owner B", "Aspen Row", "620.00");
  await openLease(page, "Aspen Row, Unit 2");
  const leasing = await leasePanel(page, "Take leasing fee");
  await leasing.getByLabel("From owner").selectOption({ label: "Demo Owner B" });
  await leasing.getByRole("button", { name: "Take leasing fee" }).click();
  await expect(page.getByRole("main")).toContainText("A leasing fee of $620.00 is taken.");
  await expect((await ownerProperty(page, "Demo Owner B", "Aspen Row")).getByRole("cell").nth(2)).toHaveText("$200.00");

  // A second owner of Aspen Row, held in the new operating account, from its page.
  await openProperty(page, "Aspen Row");
  const second = await setupPanel(page, "Add owner");
  await second.getByRole("combobox", { name: "Owner", exact: true }).selectOption({ label: "Demo Owner A" });
  await second.getByRole("combobox", { name: "Held in" }).selectOption({ label: "Second operating account" });
  await second.getByRole("button", { name: "Add owner" }).click();
  await expect(owners.getByRole("row", { name: "Demo Owner A" })).toContainText("Second operating account");
  // Their terms are set from their own page too.
  await openOwner(page, "Demo Owner A");
  const theirs = await setupPanel(page, "Set terms", "Set management terms");
  await expect(theirs.getByRole("combobox", { name: "For" }).getByRole("option", { name: "Demo Owner A, Aspen Row" })).toHaveCount(1);
});

test("staff open a report from Reports, see it on screen narrowed to one property, export it in each format, take an owner's report over last year, download the whole ledger, and every staff report in each format", async ({ page }) => {
  const people = seed.features.downloads!;
  await joinAsStaff(page, people.manager);

  // The rent roll on screen, then narrowed to Cedar House: the other properties' lines and the
  // totals across them are left out, and it says so.
  await section(page, "Reports");
  await page.getByRole("list", { name: "Rentals" }).getByRole("link", { name: "Rent roll" }).click();
  await expect(page.getByRole("heading", { level: 1, name: "Rent roll" })).toBeVisible();
  const roll = page.getByRole("table", { name: "Rent roll" });
  await expect(roll).toContainText("Aspen Row");
  await expect(roll).toContainText("Cedar House");
  await page.getByRole("combobox", { name: "Properties shown" }).selectOption({ label: "Cedar House" });
  await expect(roll).toContainText("narrowed to Cedar House: lines across all properties left out");
  await expect(roll).not.toContainText("Aspen Row");
  await downloads(page, "PDF", PDF);
  await downloads(page, "Excel", XLSX);
  await downloads(page, "CSV", CSV);
  const narrowed = (await page.getByRole("link", { name: "CSV", exact: true }).getAttribute("href")) ?? "";
  expect(narrowed).toContain("property=");
  const csv = await (await page.request.get(narrowed)).text();
  expect(csv).toContain("Cedar House");
  expect(csv).not.toContain("Aspen Row");

  // The trial balance narrowed to one owner's properties (q109): their property's accounts, and
  // none of the cash the trust bank account holds for everyone.
  await section(page, "Reports");
  await page.getByRole("list", { name: "Financial" }).getByRole("link", { name: "Trial balance" }).click();
  await page.getByRole("combobox", { name: "Properties shown" }).selectOption({ label: "All properties" });
  const trial = page.getByRole("table", { name: "Trial balance" });
  await expect(trial).toContainText("book cash");
  await page.getByRole("combobox", { name: "Owner" }).selectOption({ label: "Demo Owner B" });
  await expect(trial).toContainText("narrowed to Demo Owner B's properties: lines across all properties left out");
  await expect(trial).toContainText("owner_property: Demo Owner B / Aspen Row");
  await expect(trial).not.toContainText("book cash");
  await expect(trial).not.toContainText("Demo Owner A");
  expect(await page.getByRole("link", { name: "CSV", exact: true }).getAttribute("href")).toContain("owner=");

  // An owner's income statement over last year.
  await section(page, "Reports");
  await page.getByRole("list", { name: "Financial" }).getByRole("link", { name: "Income statement", exact: true }).click();
  await page.getByRole("combobox", { name: "Owner" }).selectOption({ label: "Demo Owner B" });
  await page.getByRole("combobox", { name: "Days" }).selectOption({ label: "Last year" });
  const year = new Date().getUTCFullYear() - 1;
  await expect(page.getByLabel("From")).toHaveValue(`${year}-01-01`);
  await expect(page.getByRole("table", { name: "Income statement" })).toContainText("net income");
  await downloads(page, "PDF", PDF);

  // Every owner's in one (q108): each property named with its owner, the lines across them all.
  await section(page, "Reports");
  await page.getByRole("list", { name: "Financial" }).getByRole("link", { name: "Income statement, all owners" }).click();
  const everyone = page.getByRole("table", { name: "Income statement, all owners" });
  await expect(everyone).toContainText("all owners");
  await expect(everyone).toContainText("Aspen Row, Demo Owner B");
  await expect(everyone).toContainText("all properties: net income");
  await downloads(page, "PDF", PDF);

  // The 1099 summary (q060): the vendors marked for a 1099, against the year's IRS threshold.
  await section(page, "Reports");
  await page.getByRole("list", { name: "Vendors" }).getByRole("link", { name: "1099 summary" }).click();
  const summary = page.getByRole("table", { name: "1099 summary" });
  await expect(summary).toContainText("a 1099 for a vendor marked for one and paid this much or more");
  await expect(summary).toContainText("not marked for a 1099");

  // Vacancy (q084): Aspen Row's Unit 2, never leased, is vacant since it was added.
  await section(page, "Reports");
  await page.getByRole("list", { name: "Rentals" }).getByRole("link", { name: "Vacancy" }).click();
  const vacancy = page.getByRole("table", { name: "Vacancy" });
  await expect(vacancy).toContainText("Aspen Row");
  await expect(vacancy).toContainText("vacant since it was added to the PMS");

  // The same owner on the accrual basis (q115): what was charged and billed this month, and
  // their balance sheet today, its equity what they hold, are owed and owe.
  await section(page, "Reports");
  await page.getByRole("list", { name: "Financial" }).getByRole("link", { name: "Income statement, accrual basis" }).click();
  await page.getByRole("combobox", { name: "Owner" }).selectOption({ label: "Demo Owner B" });
  const accrual = page.getByRole("table", { name: "Income statement, accrual basis" });
  await expect(accrual).toContainText("accrual basis");
  await expect(accrual).toContainText("income: rent");
  await downloads(page, "Excel", XLSX);
  await section(page, "Reports");
  await page.getByRole("list", { name: "Owners" }).getByRole("link", { name: "Owner balance sheet" }).click();
  await page.getByRole("combobox", { name: "Owner" }).selectOption({ label: "Demo Owner B" });
  const sheet = page.getByRole("table", { name: "Owner balance sheet" });
  await expect(sheet).toContainText("Aspen Row");
  await expect(sheet).toContainText("owner's equity");
  await downloads(page, "PDF", PDF);

  // The whole ledger, under Settings, and everything the company keeps (q132; the manager is an
  // admin): its records as CSV and its files, never a credential.
  await settings(page, "Data export");
  await downloads(page, "Whole ledger (CSV, zip)", "application/zip");
  await downloads(page, "Everything (CSV and files, zip)", "application/zip");

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

test("staff keep a vendor's W-9, a bill's receipt, a move-out photo and a property's document from Files, narrow the list, refuse a file that isn't what it says, and remove one", async ({
  page,
}) => {
  const people = seed.features.documents!;
  await joinAsStaff(page, people.bookkeeper);
  await section(page, "Files");
  // Upload: the file, what it is attached to (a bill or a lease is named with its day; pick it
  // by what it starts with) and its category.
  const keep = async (about: string, record: string, category: string, file: { name: string; mimeType: string; buffer: Buffer }) => {
    await page.getByRole("button", { name: "Upload", exact: true }).click();
    const panel = page.getByRole("dialog", { name: "Upload" });
    await panel.getByLabel("File").setInputFiles(file);
    await panel.getByRole("combobox", { name: "Attached to" }).selectOption({ label: about });
    const select = panel.getByRole("combobox", { name: about, exact: true });
    const value = await select.locator("option").filter({ hasText: record }).first().getAttribute("value");
    await select.selectOption(value ?? "");
    await panel.getByRole("combobox", { name: "Category" }).selectOption({ label: category });
    await panel.getByRole("button", { name: "Upload", exact: true }).click();
    return panel;
  };
  const pdf = (text: string) => Buffer.from(`%PDF-1.4\n% ${text}\n`);
  const png = Buffer.from("89504e470d0a1a0a0000000d49484452", "hex");
  await keep("Vendor", "Demo Plumbing Co", "W-9", { name: "w9-plumbing.pdf", mimeType: "application/pdf", buffer: pdf("synthetic W-9") });
  await expect(page.getByText("Uploaded w9-plumbing.")).toBeVisible();
  await keep("Bill", "Demo Roofing Co RF-2207", "Receipt", { name: "roof-receipt.pdf", mimeType: "application/pdf", buffer: pdf("synthetic receipt") });
  await expect(page.getByText("Uploaded roof-receipt.")).toBeVisible();
  await keep("Lease", "Aspen Row / Unit 1: Drew Sample", "Move-out photo", { name: "kitchen.png", mimeType: "image/png", buffer: png });
  await expect(page.getByText("Uploaded kitchen.")).toBeVisible();
  await keep("Property", "Cedar House", "Property document", { name: "deed.pdf", mimeType: "application/pdf", buffer: pdf("synthetic deed") });
  await expect(page.getByText("Uploaded deed.")).toBeVisible();
  const kept = page.getByRole("table", { name: "Files" }).locator("tbody tr");
  await expect(kept).toHaveCount(4);
  const deed = page.getByRole("row", { name: "deed" });
  await expect(deed).toContainText("Property document");
  await expect(deed.getByRole("link", { name: "Cedar House" })).toHaveAttribute("href", /^\/properties\//);

  // Narrowed by category, then to Aspen Row: the move-out photo's lease is there; the W-9's
  // vendor is at no property.
  await page.getByRole("combobox", { name: "Category" }).selectOption({ label: "Receipt" });
  await expect(kept).toHaveCount(1);
  await page.getByRole("combobox", { name: "Category" }).selectOption({ label: "All categories" });
  await page.getByRole("combobox", { name: "Properties shown" }).selectOption({ label: "Aspen Row" });
  await expect(page.getByRole("row", { name: "kitchen" })).toBeVisible();
  await expect(page.getByRole("row", { name: "w9-plumbing" })).toHaveCount(0);
  await page.getByRole("combobox", { name: "Properties shown" }).selectOption({ label: "All properties" });
  await expect(kept).toHaveCount(4);

  // A file whose bytes aren't the type its name says is refused, and nothing is kept.
  const refused = await keep("Vendor", "Demo Plumbing Co", "W-9", { name: "not-a.pdf", mimeType: "application/pdf", buffer: Buffer.from("plain text") });
  await expect(refused.getByRole("alert")).toHaveText("the file isn't a PDF");
  await refused.getByRole("button", { name: "Close" }).click();
  await expect(kept).toHaveCount(4);

  // Removed from its row's menu, it leaves the list (a removal is kept as a row; nothing is
  // deleted).
  await page.getByRole("button", { name: "Actions for kitchen" }).click();
  await page.getByRole("menuitem", { name: "Remove" }).click();
  await page.getByRole("alertdialog").getByRole("button", { name: "Remove" }).click();
  await expect(page.getByText("Removed kitchen.")).toBeVisible();
  await expect(kept).toHaveCount(3);
  const file = await page.request.get((await page.getByRole("link", { name: "w9-plumbing" }).getAttribute("href")) ?? "");
  expect(file.status()).toBe(200);
  expect(Buffer.from(await file.body()).equals(pdf("synthetic W-9"))).toBe(true);

  // The property's page has its document on its Files card.
  await deed.getByRole("link", { name: "Cedar House" }).click();
  await expect(page.getByRole("list", { name: "Files, Cedar House" })).toContainText("deed");

  // The vendor's page has their W-9s on its Files tab, and takes another there.
  await section(page, "Vendors");
  await expect(page.getByRole("row", { name: "Demo Plumbing Co", exact: true })).toContainText("On file");
  await page.getByRole("row", { name: "Demo Plumbing Co", exact: true }).click();
  await page.getByRole("tab", { name: "Files" }).click();
  const files = page.getByRole("list", { name: "Files, Demo Plumbing Co" });
  await expect(files).toContainText("w9-plumbing");
  await page.getByLabel("W-9 file").setInputFiles({ name: "w9-2026.pdf", mimeType: "application/pdf", buffer: pdf("synthetic W-9 2026") });
  await page.getByRole("button", { name: "Upload" }).click();
  await expect(page.getByText("Uploaded w9-2026.")).toBeVisible();
  await expect(files.getByRole("listitem")).toHaveCount(2);
});

test("staff carry over what the old system paid each vendor this year, and the year's vendor payments count it", async ({
  page,
}) => {
  const people = seed.features.switching!;
  await joinAsStaff(page, people.manager);
  await settings(page, "Import from another PMS");
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

  // The demo's expense accounts in Settings, Chart of accounts, and one more.
  await settings(page, "Chart of accounts");
  const accounts = page.getByRole("table", { name: "Chart of accounts" });
  await expect(accounts.getByRole("row", { name: "Repairs" })).toContainText("14: Repairs");
  const account = await setupPanel(page, "Add account");
  await account.getByLabel("Expense account").fill("Insurance");
  await account.getByRole("combobox", { name: "Schedule E line" }).selectOption({ label: "9: Insurance" });
  await account.getByRole("button", { name: "Add account" }).click();
  await expect(accounts.getByRole("row", { name: "Insurance" })).toContainText("9: Insurance");

  // A water bill entered in Water; a hose in none, then put in Water; a repair split across
  // Repairs and Water. All set aside.
  await section(page, "Bills");
  const aspenRow = "Demo Owner B, Aspen Row";
  await recordBill(page, "Demo Plumbing Co", aspenRow, "WTR-1", "45.00", "Water, last month", async (panel) => {
    await panel.getByRole("combobox", { name: "Expense account", exact: true })
      .selectOption({ label: "Water (Schedule E line 17: Utilities)" });
  });
  await recordBill(page, "Demo Plumbing Co", aspenRow, "HOSE-1", "12.00", "Garden hose");
  await recordBill(page, "Demo Plumbing Co", aspenRow, "FIX-1", "30.00", "Valve and its water", async (panel) => {
    await panel.getByLabel("Split across expense accounts").check();
    await panel.getByLabel("Expense account, line 1").selectOption({ label: "Repairs (Schedule E line 14: Repairs)" });
    await panel.getByLabel("Amount, line 1").fill("20.00");
    await panel.getByLabel("Expense account, line 2").selectOption({ label: "Water (Schedule E line 17: Utilities)" });
    await panel.getByLabel("Amount, line 2").fill("10.00");
    await expect(panel.getByTestId("lines-total")).toHaveText("Lines come to $30.00 of $30.00.");
  });
  const bill = (reference: string) => billRow(page, "Demo Plumbing Co", reference);
  await bill("FIX-1").getByRole("cell").first().click();
  await expect(page.getByRole("table", { name: "Lines" }).getByRole("row")).toHaveText([
    "Expense accountAmount", "Repairs$20.00", "Water$10.00",
  ]);
  await section(page, "Bills");
  await bill("HOSE-1").getByRole("cell").first().click();
  await expect(page.getByRole("table", { name: "Lines" })).toContainText("None yet");
  await page.getByRole("button", { name: "Change" }).click();
  const moving = page.getByRole("dialog", { name: "Expense accounts" });
  await moving.getByRole("combobox", { name: "Expense account" }).selectOption({ label: "Water (Schedule E line 17: Utilities)" });
  await moving.getByRole("button", { name: "Save" }).click();
  await expect(page.getByText("HOSE-1 is in one expense account.")).toBeVisible();
  await expect(page.getByRole("table", { name: "Lines" })).toContainText("Water$12.00");
  await section(page, "Bills");
  for (const reference of ["WTR-1", "HOSE-1", "FIX-1"]) {
    await (await billStep(page, "Demo Plumbing Co", reference, "Set aside")).getByRole("button", { name: "Set aside" }).click();
    await expect(bill(reference).getByTestId("status")).toHaveText("Set aside");
  }

  // A lawn service every month, in Landscaping, then moved to Repairs.
  await page.getByRole("tab", { name: "Recurring" }).click();
  await page.getByRole("button", { name: "Add recurring bill" }).click();
  const adding = page.getByRole("dialog", { name: "Add recurring bill" });
  await adding.getByRole("combobox", { name: "Vendor" }).selectOption({ label: "Demo Roofing Co" });
  await adding.getByRole("combobox", { name: "Owner and property" }).selectOption({ label: aspenRow });
  await adding.getByLabel("Ref # prefix").fill("LAWN");
  await adding.getByLabel("Monthly amount").fill("40.00");
  await adding.getByLabel("Memo").fill("Lawn service");
  await adding.getByRole("combobox", { name: "Expense account" })
    .selectOption({ label: "Landscaping (Schedule E line 7: Cleaning and maintenance)" });
  await adding.getByRole("button", { name: "Add recurring bill" }).click();
  const lawn = page.getByRole("row", { name: "Demo Roofing Co LAWN" });
  await expect(lawn.getByRole("cell").nth(7)).toHaveText("Landscaping");
  await lawn.getByRole("button", { name: "Actions for Demo Roofing Co LAWN" }).click();
  await page.getByRole("menuitem", { name: "Change expense account" }).click();
  const recurringAccount = page.getByRole("dialog", { name: "Expense account" });
  await recurringAccount.getByRole("combobox", { name: "Expense account" }).selectOption({ label: "Repairs (Schedule E line 14: Repairs)" });
  await recurringAccount.getByRole("button", { name: "Save" }).click();
  await expect(page.getByText("LAWN is entered in Repairs from now on.")).toBeVisible();

  // Staff can download any owner's income statement from the owner's Statements tab.
  await openOwner(page, "Demo Owner B");
  await page.getByRole("tab", { name: "Statements", exact: true }).click();
  const statement = page.getByRole("link", { name: "Income statement this year (PDF)" });
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
  // The split repair's water is Water too: 45.00 + 12.00 + 10.00.
  await expect(aspen.getByRole("row", { name: "Water $67.00" })).toBeVisible();
  await expect(card.getByRole("list", { name: "Schedule E, Aspen Row" })).toContainText(
    "Schedule E line 17: Utilities$67.00",
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
  await journal(page);
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
  const entry = (p: Page, memo: string) => p.getByRole("listitem", { name: `${us(today())} ${memo}` });
  const moved = entry(page, "Birch Flats rent deposited as Aspen Row");
  await expect(moved.getByTestId("status")).toHaveText("waiting");
  // Whoever proposed it doesn't post it.
  await expect(moved.getByRole("button", { name: "Post" })).toHaveCount(0);
  await expect(moved).toContainText("You proposed it: another staff member who may approve posts it.");

  const manager = await (await browser.newContext()).newPage();
  await joinAsStaff(manager, people.manager);
  await journal(manager);
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

test("staff budget a property's year in one grid, save it at once, see it against what happened and export it", async ({ page }) => {
  const people = seed.features.budgets!;
  await joinAsStaff(page, people.manager);
  await section(page, "Budgets");
  const year = Number(today().slice(0, 4));
  await page.getByRole("combobox", { name: "Property" }).selectOption({ label: "Aspen Row" });
  const grid = page.getByRole("table", { name: "Budget" });
  await expect(grid.getByLabel("Income: rent, total")).toHaveValue("0.00");

  // A year's rent typed as its total spreads over the months; repairs month by month.
  await grid.getByLabel("Income: rent, total").fill("24000");
  await grid.getByLabel("Income: rent, total").press("Enter");
  await expect(grid.getByLabel("Income: rent, June")).toHaveValue("2000.00");
  await grid.getByLabel("Expense: Repairs, March").fill("400");
  await grid.getByLabel("Expense: Repairs, September").fill("600");
  await expect(grid.getByRole("row", { name: "Net income" })).toContainText("$23,000.00");
  await page.getByRole("button", { name: "Save budget" }).click();
  await expect(page.getByText(`Saved Aspen Row's ${year} budget: 14 months changed.`)).toBeVisible();
  // Saving the same again changes nothing.
  await page.getByRole("button", { name: "Save budget" }).click();
  await expect(page.getByText(`Saved Aspen Row's ${year} budget: 0 months changed.`)).toBeVisible();

  await page.getByRole("tab", { name: "Budget vs actual" }).click();
  const versus = page.getByRole("table", { name: "Budget vs actual" });
  await expect(versus.getByRole("row", { name: "Income: rent" })).toContainText("$24,000.00");
  await expect(versus.getByRole("row", { name: "Expense: Repairs" })).toContainText("$1,000.00");

  await downloads(page, "Export", XLSX);
  const href = (await page.getByRole("link", { name: "Export", exact: true }).getAttribute("href")) ?? "";
  const csv = await page.request.get(href.replace("format=xlsx", "format=csv"));
  expect(await csv.text()).toContain("Aspen Row,income: rent,24000.00,");
});

test("an admin makes a custom role and gives it: its holder changes only what it allows, and still sees everything", async ({
  browser,
  page,
}) => {
  const people = seed.features["custom-roles"]!;
  await joinAsStaff(page, people.manager);
  await settings(page, "Staff and roles");
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
  await bookkeeper.getByRole("button", { name: "Record bill" }).click();
  const recording = bookkeeper.getByRole("dialog", { name: "Record bill" });
  await recording.getByRole("combobox", { name: "Vendor", exact: true }).selectOption({ label: "Demo Plumbing Co" });
  await recording.getByLabel("Ref #").fill("PLB-4001");
  await recording.getByLabel("Amount", { exact: true }).fill("80.00");
  await recording.getByLabel("Memo").fill("Drain");
  await recording.getByRole("button", { name: "Record bill" }).click();
  await expect(recording.getByRole("alert")).toHaveText("your roles don't let you change bills: an admin can add it");

  // Once the role allows bills too, they may.
  await role.getByLabel("Leasing agent: bills").click();
  await expect(page.getByText("Leasing agent may now change bills.")).toBeVisible();
  await recording.getByRole("button", { name: "Record bill" }).click();
  await expect(bookkeeper.getByText("Bill PLB-4001 recorded.")).toBeVisible();
});

// A day some whole days from today (UTC).
const daysFromToday = (days: number) => new Date(Date.now() + days * 86_400_000).toISOString().slice(0, 10);

test("staff add a vendor with their trade, 1099 and how they are paid, record a vendor's insurance on their page, see it running out on Today, and record the renewal", async ({ page }) => {
  const people = seed.features.vendors!;
  await joinAsStaff(page, people.manager);
  await section(page, "Vendors");
  const plumbing = page.getByRole("row", { name: "Demo Plumbing Co", exact: true });
  await expect(plumbing).toContainText("Plumbing");
  await expect(plumbing.getByTestId("standing")).toHaveText("None on file");

  // Add vendor: a side panel, then their page.
  await page.getByRole("button", { name: "Add vendor" }).click();
  const adding = page.getByRole("dialog", { name: "Add vendor" });
  await adding.getByLabel("Vendor's name").fill("Demo Glass Co");
  await adding.getByLabel("Trade (optional)").fill("Glazing");
  await adding.getByLabel("Paid by").selectOption({ label: "EFT" });
  await adding.getByLabel("1099 vendor").check();
  await adding.getByLabel("Email (optional)").fill("glass@example.com");
  await adding.getByRole("button", { name: "Add vendor" }).click();
  await expect(page.getByRole("heading", { level: 1, name: "Demo Glass Co" })).toBeVisible();
  await expect(page.getByLabel("Summary")).toContainText("Glazing");
  await expect(page.getByLabel("Summary")).toContainText("1099 vendorYes");
  await page.getByRole("button", { name: "Edit details" }).click();
  const details = page.getByRole("dialog", { name: "Edit details" });
  await details.getByLabel("Paid by").selectOption({ label: "Check" });
  await details.getByRole("button", { name: "Save" }).click();
  await expect(page.getByText("Saved Demo Glass Co.")).toBeVisible();
  await expect(page.getByLabel("Summary")).toContainText("Paid byCheck");
  await section(page, "Vendors");
  await expect(page.getByRole("row", { name: "Demo Glass Co", exact: true })).toContainText("glass@example.com");

  // On the plumber's Insurance tab: a policy that runs out in ten days, and one that ran out
  // yesterday.
  await page.getByRole("row", { name: "Demo Plumbing Co", exact: true }).click();
  await page.getByRole("tab", { name: "Insurance" }).click();
  const record = async (coverage: string, number: string, starts: string, expires: string, limit = "") => {
    await page.getByRole("button", { name: "Add policy" }).click();
    const panel = page.getByRole("dialog", { name: "Add policy" });
    await panel.getByRole("combobox", { name: "Coverage" }).selectOption(coverage);
    await panel.getByLabel("Carrier").fill("Synthetic Mutual");
    await panel.getByLabel("Policy number").fill(number);
    await panel.getByLabel("Starts").fill(starts);
    await panel.getByLabel("Expires").fill(expires);
    await panel.getByLabel("Limit (optional)").fill(limit);
    await panel.getByRole("button", { name: "Record policy" }).click();
    return panel;
  };
  await record("general liability", "GL-0001", firstOfMonthsAgo(11), daysFromToday(10));
  await expect(page.getByText("Demo Plumbing Co's general liability policy GL-0001 is recorded.")).toBeVisible();
  await record("workers compensation", "WC-0001", firstOfMonthsAgo(23), daysFromToday(-1), "1000000");
  await expect(page.getByText("Demo Plumbing Co's workers compensation policy WC-0001 is recorded.")).toBeVisible();
  const liability = page.getByRole("row", { name: "general liability" });
  await expect(liability).toContainText("Expires in 10 days");
  const workers = page.getByRole("row", { name: "workers compensation" });
  await expect(workers).toContainText("Expired");
  await expect(workers).toContainText("$1,000,000.00");
  await expect(page.getByTestId("standing")).toHaveText("Insurance: Expired");

  // A policy can't expire before it starts, and the refusal says so.
  const wrong = await record("other", "OT-0001", daysFromToday(5), daysFromToday(4));
  await expect(wrong.getByRole("alert")).toHaveText("a policy expires no earlier than it starts");
  await wrong.getByRole("button", { name: "Close" }).click();

  // Today lists both; the renewal takes the place of the policy running out.
  await section(page, "Dashboard");
  await expect(page.getByText(`Demo Plumbing Co, general liability: expires in 10 days (${us(daysFromToday(10))})`)).toBeVisible();
  await expect(page.getByText(`Demo Plumbing Co, workers compensation: expired (${us(daysFromToday(-1))})`)).toBeVisible();
  await section(page, "Vendors");
  await page.getByRole("row", { name: "Demo Plumbing Co", exact: true }).click();
  await page.getByRole("tab", { name: "Insurance" }).click();
  await record("general liability", "GL-0002", today(), daysFromToday(365));
  await expect(page.getByText("Demo Plumbing Co's general liability policy GL-0002 is recorded.")).toBeVisible();
  await expect(liability).toContainText("Current");
  await expect(liability).toContainText("GL-0002");

  // The vendor insurance report is on Reports.
  await section(page, "Reports");
  await page.getByRole("list", { name: "Vendors" }).getByRole("link", { name: "Vendor insurance" }).click();
  await expect(page.getByRole("table", { name: "Vendor insurance" })).toContainText("GL-0002");
  await downloads(page, "PDF", PDF);
  const href = (await page.getByRole("link", { name: "PDF", exact: true }).getAttribute("href")) ?? "";
  const csv = await (await page.request.get(href.replace("format=pdf", "format=csv"))).text();
  expect(csv).toContain(`Demo Plumbing Co,`);
  expect(csv).toContain(`general liability,Synthetic Mutual,GL-0002,${today()},${daysFromToday(365)},,current`);
  expect(csv).toContain("no insurance on file,Demo Roofing Co,");
});

test("staff record a lease's move-in and move-out inspections room by room, and download them side by side", async ({ page }) => {
  const people = seed.features.inspections!;
  await joinAsStaff(page, people.manager);
  await section(page, "Move-outs");
  const lease = page.getByRole("region", { name: "Aspen Row / Unit 1: Drew Sample" });
  await lease.getByRole("button", { name: "Inspections" }).click();
  const panel = page.getByRole("region", { name: "Inspections, Aspen Row / Unit 1: Drew Sample" });
  await expect(panel).toContainText("No inspection recorded yet.");

  // Move-in, the day the lease started: every room good, the keys noted.
  await panel.getByLabel("Inspected on").fill(firstOfMonthsAgo(11));
  await panel.getByLabel("Notes", { exact: true }).fill("Two keys given");
  await panel.getByRole("button", { name: "Record inspection" }).click();
  await expect(panel).toContainText(`The move-in inspection of ${us(firstOfMonthsAgo(11))} is recorded.`);
  await expect(panel).toContainText(`Move-in, ${us(firstOfMonthsAgo(11))}: 4 rooms`);

  // Move-out today: the kitchen damaged, and a hallway the move-in didn't list.
  await panel.getByRole("combobox", { name: "Inspection", exact: true }).selectOption("move-out");
  await panel.getByLabel("Inspected on").fill(today());
  await panel.getByRole("combobox", { name: "Room 1 condition" }).selectOption("damaged");
  await panel.getByLabel("Room 1 note").fill("Burn on the counter");
  await panel.getByRole("button", { name: "Add a room" }).click();
  await panel.getByLabel("Room 5", { exact: true }).fill("kitchen");
  await panel.getByRole("button", { name: "Record inspection" }).click();
  await expect(panel.getByRole("alert")).toHaveText("an inspection names each room once");
  await panel.getByLabel("Room 5", { exact: true }).fill("Hallway");
  await panel.getByRole("combobox", { name: "Room 5 condition" }).selectOption("missing");
  await panel.getByLabel("Room 5 note").fill("Smoke detector gone");
  await panel.getByRole("button", { name: "Record inspection" }).click();
  await expect(panel).toContainText(`The move-out inspection of ${us(today())} is recorded.`);
  await expect(panel).toContainText(`Move-out, ${us(today())}: 5 rooms, 2 damaged or missing`);
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

  // The demo took the month before last's fees out of trust and left last month's: Today says
  // so, with the day they are to be taken out by.
  await section(page, "Dashboard");
  const waiting = page.getByRole("region", { name: "Company fees in trust" });
  await expect(waiting).toContainText("Demo operating trust account: $");
  await expect(waiting).toContainText("of the company's fees, the oldest since");
  await waiting.getByRole("link", { name: "Open Banking" }).click();

  // Banking: the operating account's ⋯ menu transfers them out, in a side panel.
  const row = page.getByRole("row", { name: "Demo operating trust account", exact: true });
  await expect(row.getByText(/^take out/)).toBeVisible();
  const transfer = async () => {
    await row.getByRole("button", { name: "Actions for Demo operating trust account" }).click();
    await page.getByRole("menuitem", { name: "Transfer company fees" }).click();
    return page.getByRole("dialog", { name: "Transfer company fees" });
  };
  let operating = await transfer();
  const held = operating.getByTestId("company-fees");
  await expect(held).toContainText("of the company's fees, the oldest since");
  const before = Number(((await held.textContent()) ?? "").match(/^\$([\d,]+\.\d\d)/)?.[1]?.replace(/,/g, ""));
  expect(before).toBeGreaterThan(10);

  // More than is there is refused, in words; $10.00 comes out, then the rest.
  const asked = (before + 0.01).toFixed(2);
  await operating.getByLabel("Amount (leave empty for all)").fill(asked);
  await operating.getByRole("button", { name: "Take out company fees" }).click();
  await page.getByRole("alertdialog").getByRole("button", { name: "Confirm" }).click();
  await expect(operating.getByRole("alert")).toHaveText(
    `only $${before.toLocaleString("en-US", { minimumFractionDigits: 2 })} of the company's fees is in this trust bank account; $${Number(asked).toLocaleString("en-US", { minimumFractionDigits: 2 })} is more than that`,
  );
  await operating.getByLabel("Amount (leave empty for all)").fill("10.00");
  await operating.getByRole("button", { name: "Take out company fees" }).click();
  await page.getByRole("alertdialog").getByRole("button", { name: "Confirm" }).click();
  await expect(operating).toHaveCount(0);
  await expect(page.getByRole("main")).toContainText("$10.00 of the company's fees taken out.");
  const rest = (before - 10).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  await expect(row).toContainText(`$${rest}`);
  operating = await transfer();
  await expect(operating.getByTestId("company-fees")).toContainText(`$${rest} of the company's fees`);
  await operating.getByRole("button", { name: "Take out company fees" }).click();
  await page.getByRole("alertdialog").getByRole("button", { name: "Confirm" }).click();
  await expect(page.getByRole("main")).toContainText(`$${rest} of the company's fees taken out.`);
  operating = await transfer();
  await expect(operating.getByTestId("company-fees")).toHaveText("None of the company's fees is held here.");
  await expect(operating.getByRole("button", { name: "Take out company fees" })).toHaveCount(0);
  await operating.getByRole("button", { name: "Close" }).click();

  // Nothing waits on Today any more.
  await section(page, "Dashboard");
  await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
  await expect(page.getByRole("region", { name: "Company fees in trust" })).toHaveCount(0);
});

test("staff carry over the old system's unpaid bills, once, and draws hold them back", async ({ page }) => {
  const people = seed.features["cutover-bills"]!;
  await joinAsStaff(page, people.manager);
  await settings(page, "Import from another PMS");
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
  const aspen = await ownerProperty(page, "Demo Owner B", "Aspen Row");
  await expect(aspen.getByRole("cell")).toHaveText([
    "Aspen Row", "Demo operating trust account", "$200.00", "$200.00", "$520.00", "$0.00",
  ]);
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
  await section(page, "Dashboard");
  const waiting = page.getByRole("region", { name: "Trust account signers" });
  await expect(waiting).toContainText("Demo operating trust account: no broker on file");

  await settings(page, "Staff and roles");
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
  await recordContribution(bookkeeper, "Demo Owner B", "Aspen Row", "50.00");
  await payOwner(bookkeeper, "Demo Owner B", "Aspen Row", "10.00");
  await expect(bookkeeper.getByRole("alert")).toHaveText(
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
  await bookkeeper.getByRole("button", { name: "Pay $10.00" }).click();
  await bookkeeper.getByRole("alertdialog").getByRole("button", { name: "Confirm" }).click();
  await expect(bookkeeper.getByRole("main")).toContainText("1 payout of $10.00 recorded, by check.");
  await bookkeeper.close();
});

test("staff raise a month-to-month rent after a full month's notice, and the tenant sees it coming", async ({
  browser,
  page,
}) => {
  const people = seed.features["rent-changes"]!;
  await joinAsStaff(page, people.manager);
  const house = await openLease(page, "Cedar House, House");
  await expect(house).toContainText("$2,250.00 a month");
  const change = await leasePanel(page, "Change rent");

  // From this month: notice was due by the first of last month, so it is refused, in words.
  await change.getByLabel("New monthly rent").fill("2400.00");
  await change.getByLabel("From (the first of a month)").fill(firstOfThisMonth());
  await change.getByRole("button", { name: "Change rent" }).click();
  await expect(change.getByRole("alert")).toHaveText(
    `written notice of a full month comes first: for a change from ${firstOfThisMonth()}, give it by ${firstOfMonthsAgo(1)}`,
  );
  // From the first month a notice given today allows, it is recorded.
  const from = dayOfMonth() === 1 ? firstOfMonthsAgo(-1) : firstOfMonthsAgo(-2);
  await change.getByLabel("New monthly rent").fill("2400.00");
  await change.getByLabel("From (the first of a month)").fill(from);
  await change.getByLabel("Served").selectOption("in person");
  await change.getByRole("button", { name: "Change rent" }).click();
  await expect(change).toHaveCount(0);
  await expect(house).toContainText(`Rent of $2,400.00 a month from ${us(from)} recorded.`);
  await expect(house.getByRole("list", { name: "Rent changes, House" })).toHaveText(
    `From ${us(from)}: $2,400.00 a month (notice in person on ${us(today())})`,
  );
  // Until then the rent in force stays.
  await expect(house).toContainText("$2,250.00 a month");

  const tenant = await browser.newPage();
  await joinPortal(tenant, people.taylor);
  await expect(tenant.getByText(`$2,250.00 a month; $2,400.00 from ${us(from)} (notice given ${us(today())})`)).toBeVisible();
  await tenant.close();
});

test("staff move a tenant in mid-month on a year's lease and the rent run charges its days, a late fee stops at its cap, and the lease's own terms let its rent change mid-term", async ({
  page,
}) => {
  const people = seed.features["rent-terms"]!;
  await joinAsStaff(page, people.manager);

  // Birch Flats' late fees from this month: no grace, $25.00 and 10% of what is unpaid, up to
  // $60.00 (the cap a state's law may set; the PMS knows none, so staff type it).
  await openProperty(page, "Birch Flats");
  const fees = await setupPanel(page, "Set late fees");
  await fees.getByLabel("Late fees from").fill(firstOfThisMonth());
  await fees.getByLabel("Grace days").fill("0");
  await fees.getByLabel("Flat fee").fill("25.00");
  await fees.getByLabel("Percent of unpaid").fill("10");
  await fees.getByLabel("Up to").fill("60.00");
  await fees.getByRole("button", { name: "Set late fees" }).click();
  await expect(page.getByTestId("late-fees")).toContainText(
    /After 0 days' grace: \$25\.00 and 10(\.0+)?% of what is unpaid, up to \$60\.00/,
  );

  // A year's lease on Aspen Row's vacant unit, from the 16th of this month.
  const [year, month] = today().split("-") as [string, string];
  const starts = `${year}-${month}-16`;
  const ends = `${Number(year) + 1}-${month}-15`;
  const days = new Date(Date.UTC(Number(year), Number(month), 0)).getUTCDate();
  const firstPart = DOLLARS.format(Math.round((124000 * (days - 15)) / days) / 100);
  await openProperty(page, "Aspen Row");
  await page.getByRole("row", { name: "Unit 2" }).getByRole("link", { name: "Unit 2" }).click();
  const adding = await setupPanel(page, "Add lease in place", "Add lease");
  await adding.getByLabel("Tenant's name").fill("Avery Sample");
  await adding.getByRole("button", { name: "Add tenant" }).click();
  await expect(adding.getByLabel("Avery Sample")).toBeChecked();
  await adding.getByLabel("Starts").fill(starts);
  await adding.getByLabel("Ends (empty for month to month)").fill(ends);
  await adding.getByLabel("Rent a month").fill("1240.00");
  await adding.getByRole("button", { name: "Add lease" }).click();
  await expect(page.getByRole("row", { name: "Avery Sample" })).toContainText(`${us(starts)} – ${us(ends)}`);

  // The month's run charges the six leases in effect and the new one its days from the 16th, due
  // that day; Birch Flats' rents are late from the 2nd, each fee capped at $60.00.
  await section(page, "Rent roll");
  await page.getByRole("button", { name: "Actions for the rent roll" }).click();
  await page.getByRole("menuitem", { name: "Run rent now" }).click();
  const run = page.getByRole("dialog", { name: "Run rent now" });
  await run.getByLabel("Late fees as of").fill(today());
  await run.getByRole("button", { name: "Run rent now" }).click();
  await expect(run).toHaveCount(0);
  await expect(page.getByRole("main")).toContainText(/7 rents, 1 recurring charge and \d+ late fees? charged\./);
  const unitB = await openLease(page, "Birch Flats, Unit B");
  const lateFee = `Late fee, rent due ${firstOfThisMonth()}`;
  if (dayOfMonth() > 1) {
    await expect(unitB.getByRole("list", { name: "Open charges" })).toContainText(`${lateFee}: $60.00 open`);
  } else {
    await expect(unitB.getByRole("list", { name: "Open charges" })).not.toContainText(lateFee);
  }
  await section(page, "Rent roll");
  await page.getByRole("combobox", { name: "Status" }).selectOption({ label: "All" });
  await page.getByRole("row", { name: "Aspen Row, Unit 2", exact: true }).getByRole("link", { name: "Unit 2", exact: true }).click();
  const yearLease = page.getByRole("main");
  await expect(page.getByRole("heading", { level: 1, name: /^Aspen Row · Unit 2 · / })).toBeVisible();
  await expect(yearLease.getByRole("list", { name: "Open charges" })).toContainText(
    `${us(starts)} Rent due ${starts}, ${starts} to ${year}-${month}-${days} (${days - 15} of ${days} days): ${firstPart} open`,
  );

  // Mid-term, the rent changes only as the lease's own terms allow, which staff name with it.
  const change = await leasePanel(page, "Change rent");
  const from = dayOfMonth() === 1 ? firstOfMonthsAgo(-1) : firstOfMonthsAgo(-2);
  await change.getByLabel("New monthly rent").fill("1290.00");
  await change.getByLabel("From (the first of a month)").fill(from);
  await change.getByLabel("Served").selectOption("in person");
  await change.getByLabel("The lease term that allows it").fill("Section 4: one increase a year, on a month's notice");
  await change.getByRole("button", { name: "Change rent" }).click();
  await expect(change).toHaveCount(0);
  await expect(yearLease).toContainText(`Rent of $1,290.00 a month from ${us(from)} recorded.`);
  await expect(yearLease.getByRole("list", { name: "Rent changes, Unit 2" })).toHaveText(
    `From ${us(from)}: $1,290.00 a month (notice in person on ${us(today())}; Section 4: one increase a year, on a month's notice)`,
  );
});

test("staff require renters insurance on a lease, Today flags it, the tenant records their own policy and staff its renewal", async ({
  browser,
  page,
}) => {
  const people = seed.features["renters-insurance"]!;
  await joinAsStaff(page, people.manager);
  const house = await openLease(page, "Cedar House, House");
  await expect(house.getByTestId("renters-insurance")).toContainText("Renters insurance not required: none on file");
  const terms = await leasePanel(page, "Renters insurance");
  await terms.getByLabel("Requires renters insurance").check();
  await terms.getByLabel("Least liability coverage (optional)").fill("100000.00");
  await terms.getByRole("button", { name: "Set insurance terms" }).click();
  await expect(terms).toHaveCount(0);
  await expect(house).toContainText(`Renters insurance is required from ${us(today())}.`);
  await expect(house.getByTestId("renters-insurance")).toContainText(
    "Renters insurance required (at least $100,000.00 of liability): none on file",
  );

  await section(page, "Dashboard");
  const waiting = page.getByRole("region", { name: "Renters insurance" });
  await expect(waiting).toContainText("House, Cedar House (Quinn Demo, Taylor Demo): renters insurance none on file");

  // The tenant records their policy, from the insurer they chose.
  const tenant = await browser.newPage();
  await joinPortal(tenant, people.taylor);
  const card = part(tenant, "Renters insurance");
  await card.getByLabel("Insurer").fill("Synthetic Mutual");
  await card.getByLabel("Policy number").fill("RI-0001");
  await card.getByLabel("Policy runs out").fill(daysFromToday(365));
  await card.getByLabel("Liability coverage (optional)").fill("300000.00");
  await card.getByRole("button", { name: "Record policy" }).click();
  await expect(card).toContainText("Policy RI-0001 from Synthetic Mutual recorded.");
  await expect(card.getByTestId("renters-insurance")).toContainText(
    `Synthetic Mutual RI-0001 through ${us(daysFromToday(365))}, $300,000.00 of liability (current)`,
  );
  await tenant.close();

  await page.reload();
  await expect(page.getByRole("region", { name: "Renters insurance" })).toHaveCount(0);

  // The tenant mails in next year's renewal; staff record it, and this year's still counts.
  await openLease(page, "Cedar House, House");
  const policy = await leasePanel(page, "Renters insurance");
  await policy.getByLabel("Insurer").fill("Synthetic Mutual");
  await policy.getByLabel("Policy number").fill("RI-0002");
  await policy.getByLabel("Policy starts").fill(daysFromToday(366));
  await policy.getByLabel("Policy runs out").fill(daysFromToday(730));
  await policy.getByLabel("Liability coverage (optional)", { exact: true }).fill("300000.00");
  await policy.getByLabel("The landlord is named an interested party").click();
  await policy.getByRole("button", { name: "Record policy" }).click();
  await expect(policy).toHaveCount(0);
  await expect(house).toContainText("Policy RI-0002 from Synthetic Mutual recorded.");
  await expect(house.getByTestId("renters-insurance")).toContainText(
    `Synthetic Mutual RI-0001 through ${us(daysFromToday(365))}`,
  );
});

// A 1x1 PNG, synthetic, for the company's logo.
const LOGO = Buffer.from("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNkYAAAAAYAAjCB0C8AAAAASUVORK5CYII=", "base64");

test("staff keep the company's name, address, logo and the fees new properties start with; a property added then starts with them, and owners and tenants see their manager", async ({
  page,
  browser,
}) => {
  const people = seed.features.company!;
  await joinAsStaff(page, people.manager);
  await settings(page, "Company setup");
  await expect(page.getByText("No mailing address yet")).toBeVisible();

  // Its name and mailing address, in a side panel.
  await page.getByRole("button", { name: "Edit company" }).click();
  const editing = page.getByRole("dialog", { name: "Edit company" });
  await editing.getByLabel("Company name").fill("Demo Property Management Group");
  await editing.getByLabel("Street address").fill("100 Example Street");
  await editing.getByLabel("City").fill("Sampletown");
  await editing.getByLabel("State").fill("ZZ");
  await editing.getByLabel("ZIP code").fill("00000");
  await editing.getByRole("button", { name: "Save" }).click();
  await expect(page.getByText("Saved Demo Property Management Group.")).toBeVisible();
  await expect(page.getByLabel("Company", { exact: true })).toContainText("Sampletown, ZZ 00000");

  // Its logo, a PNG.
  await page.getByLabel("Upload a logo").setInputFiles({ name: "logo.png", mimeType: "image/png", buffer: LOGO });
  await expect(page.getByText("The logo is saved.")).toBeVisible();
  await expect(page.getByRole("img", { name: "Demo Property Management Group logo" })).toBeVisible();

  // The fees a property added from now on starts with.
  await page.getByRole("button", { name: "Set default fees" }).click();
  const fees = page.getByRole("dialog", { name: "Set default fees" });
  await fees.getByLabel("Charge late fees").check();
  await fees.getByLabel("Grace days").fill("4");
  await fees.getByLabel("Flat fee").fill("40.00");
  await fees.getByLabel("NSF fee (empty for none)").fill("30.00");
  await fees.getByRole("button", { name: "Save" }).click();
  await expect(page.getByTestId("default-late-fees")).toHaveText("After 4 days' grace: $40.00");
  await expect(page.getByTestId("default-nsf-fee")).toHaveText("NSF fee $30.00");

  // A property added now starts with them; one already there keeps its own.
  await section(page, "Properties");
  await page.getByRole("button", { name: "Add property" }).click();
  const adding = page.getByRole("dialog", { name: "Add property" });
  await adding.getByLabel("Property name").fill("Juniper Court");
  await adding.getByLabel("Owner").selectOption({ label: "Demo Owner A" });
  await adding.getByRole("button", { name: "Add property" }).click();
  await expect(page.getByRole("heading", { level: 1, name: "Juniper Court" })).toBeVisible();
  await expect(page.getByTestId("late-fees")).toContainText("After 4 days' grace: $40.00");
  await expect(page.getByTestId("nsf-fee")).toHaveText("NSF fee $30.00");
  await openProperty(page, "Aspen Row");
  await expect(page.getByTestId("nsf-fee")).toHaveText("No NSF fee");

  // An owner and a tenant see who manages their home, and where to write to them.
  for (const invite of [people.owner_a!, people.taylor!]) {
    const portal = await (await browser.newContext()).newPage();
    await joinPortal(portal, invite);
    const manager = portal.locator("section", { has: portal.getByRole("heading", { name: "Your property manager" }) });
    await expect(manager).toContainText("Demo Property Management Group100 Example StreetSampletown, ZZ 00000");
    await expect(manager.getByRole("img", { name: "Demo Property Management Group logo" })).toBeVisible();
    const logo = await portal.request.get("/api/company/logo");
    expect(logo.headers()["content-type"]).toBe("image/png");
    await portal.context().close();
  }

  // Removed, there is no logo.
  await settings(page, "Company setup");
  await page.getByRole("button", { name: "Remove logo" }).click();
  await expect(page.getByText("The logo is removed.")).toBeVisible();
  await expect(page.getByText("No logo yet")).toBeVisible();
});

test("staff email a tenant from their page and every tenant of a property, an owner resets a forgotten password by email, and each email shows on the person's Email tab", async ({
  page,
}) => {
  const people = seed.features.email!;

  // A forgotten password: sign-in offers it (the server emails), and the answer never says
  // whether the email has a login.
  await page.goto(`/sign-in?pmc=${people.pmc_id}`);
  await page.getByRole("link", { name: "Forgot your password?" }).click();
  await expect(page.getByRole("heading", { level: 1, name: "Set a new password" })).toBeVisible();
  await page.getByLabel("Email you sign in with").fill(people.emails.owner_a!);
  await page.getByRole("button", { name: "Email me a link" }).click();
  await expect(page.getByRole("status")).toContainText("a link to set a new password is on its way");

  await joinAsStaff(page, people.manager);

  // A tenant, from their page's Email tab: written in a side panel, kept on the tab.
  const search = page.getByLabel("Search people, homes, bills, work orders and files");
  await search.fill("drew");
  await page.getByRole("list", { name: "Search results" }).getByRole("link", { name: /Drew Sample/ }).click();
  await expect(page.getByRole("heading", { level: 1, name: "Drew Sample" })).toBeVisible();
  await page.getByRole("tab", { name: "Email", exact: true }).click();
  await page.getByRole("button", { name: "Write email" }).click();
  const panel = page.getByRole("dialog", { name: "Email Drew Sample" });
  await panel.getByLabel("Subject").fill("Parking lot repaving");
  await panel.getByLabel("Message").fill("Please move your car from the lot on Friday.");
  await panel.getByRole("button", { name: "Send email" }).click();
  await expect(page.getByText(/^Email sent to .+@example\.com\.$/)).toBeVisible();
  await expect(panel).toHaveCount(0);
  const drew = page.getByRole("list", { name: "Email, Drew Sample" });
  await expect(drew).toContainText("Parking lot repaving");
  // Sent by the server's (fake) sender after the request: read again, it says so.
  await page.reload();
  await page.getByRole("tab", { name: "Email", exact: true }).click();
  await expect(drew.getByRole("listitem").first()).toContainText("Sent");

  // Every tenant living at a property, from its page.
  await section(page, "Properties");
  await page.getByRole("link", { name: "Aspen Row", exact: true }).click();
  await expect(page.getByRole("heading", { level: 1, name: "Aspen Row" })).toBeVisible();
  await page.getByRole("button", { name: "Email tenants" }).click();
  const all = page.getByRole("dialog", { name: "Email tenants of Aspen Row" });
  await all.getByLabel("Subject").fill("Water shutoff Tuesday");
  await all.getByLabel("Message").fill("The water is off Tuesday from 9 to 11.");
  await all.getByRole("button", { name: "Send email" }).click();
  await expect(page.getByText(/^Emailed \d+ tenants?\./)).toBeVisible();

  // The owner's reset is on their Email tab, its link never kept.
  await section(page, "Owners");
  await page.getByRole("link", { name: "Demo Owner A", exact: true }).click();
  await page.getByRole("tab", { name: "Email", exact: true }).click();
  const owner = page.getByRole("list", { name: "Email, Demo Owner A" });
  await expect(owner).toContainText("Set a new password");
  await expect(owner).toContainText("Password reset");
});

test("staff turn on the emails the PMS writes by itself, and a run of rent sends each owner last month's statement onto their Email tab, its PDF attached; a payment sends its tenant the receipt", async ({
  page,
}) => {
  const people = seed.features["automatic-email"]!;
  await joinAsStaff(page, people.manager);
  await settings(page, "Company setup");
  const shown = page.getByLabel("Automatic emails", { exact: true });
  await expect(shown).toContainText("Monthly statements to ownersOff");

  // Owners' statements from the first of each month, and vendors' work orders.
  await page.getByRole("button", { name: "Edit automatic emails" }).click();
  const editing = page.getByRole("dialog", { name: "Automatic emails" });
  await editing.getByLabel("Monthly statements to owners").check();
  await editing.getByLabel("Day of the month they go").selectOption("1");
  await editing.getByLabel("Work orders to vendors").check();
  await editing.getByLabel("Payment receipts to tenants").check();
  await editing.getByRole("button", { name: "Save" }).click();
  await expect(page.getByText("Saved the automatic emails.")).toBeVisible();
  await expect(editing).toHaveCount(0);
  await expect(shown).toContainText("Last month's, from the 1st of each month");
  await expect(shown).toContainText("Work orders to vendorsOn");
  await expect(shown).toContainText("Payment receipts to tenantsOn, the PDF attached");

  // What runs every night, run by hand: each owner with an email gets last month's statement.
  await section(page, "Rent roll");
  await page.getByRole("button", { name: "Actions for the rent roll" }).click();
  await page.getByRole("menuitem", { name: "Run rent now" }).click();
  await page.getByRole("dialog", { name: "Run rent now" }).getByRole("button", { name: "Run rent now" }).click();
  await expect(page.getByRole("main")).toContainText(/charged\. 2 emails written\./);

  const last = new Date();
  last.setUTCDate(1);
  last.setUTCMonth(last.getUTCMonth() - 1);
  const month = last.toLocaleString("en-US", { month: "long", year: "numeric", timeZone: "UTC" });
  await section(page, "Owners");
  await page.getByRole("link", { name: "Demo Owner A", exact: true }).click();
  await page.getByRole("tab", { name: "Email", exact: true }).click();
  const owner = page.getByRole("list", { name: "Email, Demo Owner A" });
  await expect(owner).toContainText(`Your statement for ${month}`);
  await expect(owner).toContainText(`Monthly statement to ${people.emails.owner_a}`);
  await expect(owner).toContainText("The month's statement attached (PDF)");

  // A payment recorded: its tenant gets the receipt, its PDF attached (q029).
  await openLease(page, "Aspen Row, Unit 1");
  const pay = await leasePanel(page, "Receive payment");
  await pay.getByLabel("Payment received").fill("50.00");
  await pay.getByLabel("Check or reference").fill("Check 31");
  await pay.getByRole("button", { name: "Record payment" }).click();
  await expect(pay).toHaveCount(0);
  await page.getByLabel("Search people, homes, bills, work orders and files").fill("drew");
  await page.getByRole("list", { name: "Search results" }).getByRole("link", { name: /Drew Sample/ }).click();
  await page.getByRole("tab", { name: "Email", exact: true }).click();
  const drew = page.getByRole("list", { name: "Email, Drew Sample" });
  await expect(drew).toContainText("Your receipt: $50.00 received");
  await expect(drew).toContainText(`Payment receipt to ${people.emails.drew}`);
  await expect(drew).toContainText("The receipt attached (PDF)");
});

test("staff put a lease on a payment hold, accept a cashier's check past it and release it, print receipts, take payments to the bank together and print the slip; the tenant downloads their receipt", async ({
  browser,
  page,
}) => {
  const people = seed.features["rent-in"]!;
  const tenant = await (await browser.newContext()).newPage();
  await joinPortal(tenant, people.drew);
  await joinAsStaff(page, people.manager);
  const yesterday = new Date(Date.now() - 86_400_000).toISOString().slice(0, 10);
  const usYesterday = `${yesterday.slice(5, 7)}/${yesterday.slice(8, 10)}/${yesterday.slice(0, 4)}`;

  // An eviction filed: payments are refused from yesterday.
  await openLease(page, "Aspen Row, Unit 1");
  const hold = await leasePanel(page, "Place payment hold");
  await hold.getByLabel("Hold payments from").fill(yesterday);
  await hold.getByRole("button", { name: "Place hold" }).click();
  await expect(page.getByText(`On a payment hold since ${usYesterday}.`)).toBeVisible();
  await expect(page.getByRole("main")).toContainText(`Since ${usYesterday}: Eviction filed.`);

  // The PMC accepts one cashier's check past the hold.
  const pay = await leasePanel(page, "Receive payment");
  await pay.getByLabel("Payment received").fill("100.00");
  await pay.getByLabel("Method").selectOption({ label: "Cashier's check" });
  await pay.getByLabel("Check or reference").fill("CC 9");
  await pay.getByLabel("Accept this payment past the payment hold").check();
  await pay.getByRole("button", { name: "Record payment" }).click();
  await expect(pay).toHaveCount(0);

  // Released from today: the next payment needs no acceptance.
  const release = await leasePanel(page, "Release payment hold");
  await release.getByRole("button", { name: "Release hold" }).click();
  await expect(page.getByText(/^The payment hold is released from /)).toBeVisible();
  const next = await leasePanel(page, "Receive payment");
  await next.getByLabel("Payment received").fill("50.00");
  await next.getByLabel("Check or reference").fill("Check 12");
  await expect(next.getByLabel("Accept this payment past the payment hold")).toHaveCount(0);
  await next.getByRole("button", { name: "Record payment" }).click();
  await expect(next).toHaveCount(0);

  // Each payment's receipt, from the ledger.
  await leaseTab(page, "Ledger");
  const received = page.getByRole("table", { name: "Payments received" });
  await expect(received).toContainText("Cashier's check · CC 9");
  await downloads(page, "Receipt (PDF)", PDF);

  // Both go to the bank together, with a slip.
  await section(page, "Banking");
  await page.getByRole("row", { name: "Demo operating trust account", exact: true }).getByRole("link").click();
  await page.getByRole("tab", { name: "Deposits", exact: true }).click();
  const waiting = page.getByRole("list", { name: "Not deposited yet" });
  await waiting.getByLabel(/\(CC 9\): \$100\.00$/).check();
  await waiting.getByLabel(/\(Check 12\): \$50\.00$/).check();
  await page.getByRole("button", { name: "Record deposit of $150.00" }).click();
  await expect(page.getByText(/^Deposit of \$150\.00 recorded for /)).toBeVisible();
  await expect(waiting.getByText(/CC 9/)).toHaveCount(0);
  await expect(page.getByRole("table", { name: "Deposits" })).toContainText("Not matched yet");
  await downloads(page, "Slip (PDF)", PDF);

  // The tenant downloads their own receipt.
  await tenant.reload();
  const theirs = tenant.getByRole("list", { name: "Payments" });
  await expect(theirs).toContainText("Cashier's check CC 9");
  const href = (await theirs.getByRole("link", { name: "Receipt (PDF)" }).first().getAttribute("href")) ?? "";
  const answer = await tenant.request.get(href);
  expect(answer.status()).toBe(200);
  expect(answer.headers()["content-type"]).toContain(PDF);
});

test("staff waive a late fee from the lease page: it comes off that fee, and the ledger shows the waiver", async ({
  page,
}) => {
  const people = seed.features.waive!;
  await joinAsStaff(page, people.manager);
  const lease = await openLease(page, "Elm Court, Unit 1");
  const before = (await page.getByTestId("balance-due").textContent()) ?? "";

  // A late fee, then waived: the whole of it, with why.
  const charge = await leasePanel(page, "Enter charge");
  await charge.getByLabel("Add a").selectOption("fee");
  await charge.getByLabel("Amount").fill("50.00");
  await charge.getByLabel("What for").fill("Late fee");
  await charge.getByRole("button", { name: "Add" }).click();
  await expect(charge).toHaveCount(0);
  const open = lease.getByRole("list", { name: "Open charges" });
  await expect(open).toContainText("Late fee: $50.00 open");
  // The row's ⋯ menu: a click beside it closes it; then from the keyboard, the arrow keys reach
  // Waive and Enter picks it.
  const actions = open.getByRole("button", { name: /^Actions for Late fee due / });
  await actions.click();
  await expect(page.getByRole("menu")).toBeVisible();
  await page.mouse.click(5, 5); // beside the menu: the page behind is out of reach while it is open
  await expect(page.getByRole("menu")).toHaveCount(0);
  await actions.focus();
  await page.keyboard.press("ArrowDown");
  await expect(page.getByRole("menuitem", { name: "Waive" })).toBeFocused();
  await page.keyboard.press("Enter");
  await open.getByLabel("Why").fill("First time late");
  await open.getByRole("button", { name: "Waive $50.00" }).click();
  await expect(page.getByText("Waived $50.00 of Late fee.")).toBeVisible();
  await expect(page.getByText("Late fee: $50.00 open")).toHaveCount(0);
  await expect(page.getByTestId("balance-due")).toHaveText(before);

  await leaseTab(page, "Ledger");
  await expect(page.getByRole("main")).toContainText("Waived: Late fee, due");
});

test("staff take one tenant off a lease, keep who else lives there and who guarantees it, and break the lease with its fee", async ({
  page,
}) => {
  const people = seed.features["lease-people"]!;
  await joinAsStaff(page, people.manager);

  // Cedar House's leases break for $250.00 and a month's rent, for leases from 2020 on.
  await openProperty(page, "Cedar House");
  const terms = await setupPanel(page, "Set lease-break fee");
  await terms.getByLabel("For leases starting from").fill("2020-01-01");
  await terms.getByLabel("Flat fee").fill("250.00");
  await terms.getByLabel("Months of rent").fill("1");
  await terms.getByRole("button", { name: "Set lease-break fee" }).click();
  await expect(page.getByTestId("lease-break-fee")).toHaveText(
    "Lease-break fee $250.00 and 1 month's rent (leases from 01/01/2020)",
  );

  // Quinn leaves; their deposit stays in trust, moved to Taylor, who stays on.
  await openLease(page, "Cedar House, House");
  await leaseTab(page, "Tenants");
  await page.getByRole("row", { name: "Quinn Demo" }).getByRole("button", { name: "Actions for Quinn Demo" }).click();
  await page.getByRole("menuitem", { name: "Take off the lease" }).click();
  const leaving = page.getByRole("dialog", { name: "Take a tenant off the lease" });
  await leaving.getByLabel("Their $1,125.00 deposit").selectOption({ label: "Moves to Taylor Demo" });
  await leaving.getByRole("button", { name: "Take Quinn Demo off the lease" }).click();
  await expect(page.getByText("Quinn Demo left the lease; their $1,125.00 deposit moved to Taylor Demo.")).toBeVisible();
  await expect(page.getByRole("row", { name: "Quinn Demo" })).toContainText(`Left ${us(today())}`);

  // Who else is on the lease: a guarantor, an assistance animal and a car, which is taken off.
  const add = async (button: string, fields: Record<string, string>, done: string) => {
    const panel = await setupPanel(page, button);
    for (const [label, value] of Object.entries(fields)) await panel.getByLabel(label, { exact: true }).fill(value);
    if (button === "Add pet") await panel.getByRole("checkbox").check();
    await panel.getByRole("button", { name: button }).click();
    await expect(page.getByText(done)).toBeVisible();
  };
  await add("Add guarantor", { Name: "Guarantor Demo", Email: "guarantor.demo@example.com" }, "Guarantor Demo guarantees the lease.");
  await add("Add occupant", { Name: "Occupant Demo", "Relationship to the tenants": "Child" }, "Occupant Demo lives there too.");
  await add("Add pet", { Name: "Pet Demo", "Kind (dog, cat...)": "Dog" }, "Pet Demo is on the lease.");
  await add("Add vehicle", { Make: "Make Demo", Plate: "SYN-001" }, "The Make Demo is on the lease.");
  await expect(page.getByRole("table", { name: "Pets and animals" })).toContainText("Assistance animal");
  await expect(page.getByText(/no pet rent, fee or deposit is charged for it \(Fair Housing Act\)/)).toBeVisible();
  await page.getByRole("button", { name: "Actions for Make Demo, SYN-001" }).click();
  await page.getByRole("menuitem", { name: "Take off the lease" }).click();
  await page.getByRole("alertdialog").getByRole("button", { name: "Take off the lease" }).click();
  await expect(page.getByText("Make Demo, SYN-001 is off the lease.")).toBeVisible();
  await expect(page.getByRole("table", { name: "Vehicles" })).toHaveCount(0);

  // The month-to-month lease gets a last day, then ends earlier: that breaks it, and its fee is
  // charged once ticked.
  const later = new Date(Date.now() + 60 * 86_400_000).toISOString().slice(0, 10);
  const sooner = new Date(Date.now() + 30 * 86_400_000).toISOString().slice(0, 10);
  let ending = await leasePanel(page, "End lease");
  await ending.getByLabel("Last day").fill(later);
  await ending.getByRole("button", { name: "End lease" }).click();
  await expect(page.getByText(`The lease ends on ${us(later)}.`)).toBeVisible();
  ending = await leasePanel(page, "End lease");
  await ending.getByLabel("Last day").fill(sooner);
  await ending.getByLabel("Charge the lease-break fee: $2,500.00").check();
  await ending.getByRole("button", { name: "End lease" }).click();
  await expect(page.getByText(`The lease ends on ${us(sooner)}; a $2,500.00 lease-break fee was charged.`)).toBeVisible();
  // Due on the new last day: owed now, and on the lease's open charges.
  await expect(page.getByTestId("balance-due")).toHaveText("$2,500.00");
  await leaseTab(page, "Summary");
  await expect(page.getByRole("list", { name: "Open charges" })).toContainText("Lease-break fee: $2,500.00 open");
});

test("staff pick the day of the month a lease's rent is due, the rent run charges it then, and the tenant sees it", async ({
  browser,
  page,
}) => {
  const people = seed.features["rent-due-day"]!;
  const nth = (n: number) =>
    `${n}${n % 10 === 1 && n !== 11 ? "st" : n % 10 === 2 && n !== 12 ? "nd" : n % 10 === 3 && n !== 13 ? "rd" : "th"}`;
  await joinAsStaff(page, people.manager);

  // Cedar House's rent moves to the 15th from this month, not charged yet.
  const house = await openLease(page, "Cedar House, House");
  await expect(page.getByTestId("rent-due-day")).toHaveText("Due on the 1st");
  let change = await leasePanel(page, "Change rent due day");
  await change.getByLabel("Rent due on day").fill("15");
  await change.getByLabel("From (the first of a month)").fill(firstOfThisMonth());
  await change.getByRole("button", { name: "Change due day" }).click();
  await expect(change).toHaveCount(0);
  await expect(house).toContainText(`Rent due on the 15th from ${us(firstOfThisMonth())}.`);
  await expect(page.getByTestId("rent-due-day")).toHaveText("Due on the 15th");
  await expect(house.getByRole("list", { name: "Rent due day, House" })).toHaveText(
    `From ${us(firstOfThisMonth())}: due on the 15th of each month`,
  );
  // Once a month: the same month again is refused, in words.
  change = await leasePanel(page, "Change rent due day");
  await change.getByLabel("Rent due on day").fill("20");
  await change.getByLabel("From (the first of a month)").fill(firstOfThisMonth());
  await change.getByRole("button", { name: "Change due day" }).click();
  await expect(change.getByRole("alert")).toHaveText("this lease's due day changes from that month already");
  await change.getByRole("button", { name: "Close" }).click();

  const tenant = await browser.newPage();
  await joinPortal(tenant, people.taylor);
  await expect(tenant.getByText("Due on the 15th")).toBeVisible();
  await tenant.close();

  // A lease in place on Aspen Row's vacant unit from the first of this month, due on today's day
  // (the 28th at most).
  const due = Math.min(dayOfMonth(), 28);
  const dueOn = `${today().slice(0, 8)}${String(due).padStart(2, "0")}`;
  await openProperty(page, "Aspen Row");
  await page.getByRole("row", { name: "Unit 2" }).getByRole("link", { name: "Unit 2" }).click();
  const lease = await setupPanel(page, "Add lease in place", "Add lease");
  await lease.getByLabel("Tenant's name").fill("Avery Sample");
  await lease.getByRole("button", { name: "Add tenant" }).click();
  await expect(lease.getByLabel("Avery Sample")).toBeChecked();
  await lease.getByLabel("Starts").fill(firstOfThisMonth());
  await lease.getByLabel("Rent a month").fill("1240.00");
  await lease.getByLabel("Rent due on day (1 to 28)").fill(String(due));
  await lease.getByRole("button", { name: "Add lease" }).click();
  await expect(page.getByRole("row", { name: "Avery Sample" })).toContainText("$1,240.00");

  // Run by hand today, the rent run charges it: due on its day.
  await section(page, "Rent roll");
  await page.getByRole("button", { name: "Actions for the rent roll" }).click();
  await page.getByRole("menuitem", { name: "Run rent now" }).click();
  const run = page.getByRole("dialog", { name: "Run rent now" });
  await run.getByLabel("Late fees as of").fill(today());
  await run.getByRole("button", { name: "Run rent now" }).click();
  await expect(run).toHaveCount(0);
  await expect(page.getByRole("row", { name: "Aspen Row, Unit 2", exact: true }).getByTestId("balance")).toHaveText(
    "$1,240.00",
  );
  const aspen = await openLease(page, "Aspen Row, Unit 2");
  await expect(page.getByTestId("rent-due-day")).toHaveText(`Due on the ${nth(due)}`);
  await expect(aspen.getByRole("list", { name: "Open charges" })).toContainText(
    `${us(dueOn)} Rent due ${dueOn}: $1,240.00 open`,
  );
  // This month is charged: a new due day starts after it.
  change = await leasePanel(page, "Change rent due day");
  await change.getByLabel("Rent due on day").fill("5");
  await change.getByLabel("From (the first of a month)").fill(firstOfThisMonth());
  await change.getByRole("button", { name: "Change due day" }).click();
  await expect(change.getByRole("alert")).toHaveText(
    `rent due ${dueOn} is charged already; a new due day starts after its month`,
  );
});

test("a company has its management fees taken each month, staff set a flat fee and take an inspection fee from an owner", async ({
  page,
}) => {
  const people = seed.features["owner-fees"]!;
  await joinAsStaff(page, people.manager);

  // Company setup: the nightly run takes last month's management fees by itself, from the 1st.
  await settings(page, "Company setup");
  await expect(page.getByTestId("automatic-fees")).toHaveText("Collected by hand on Management fees");
  await page.getByRole("button", { name: "Edit monthly fees" }).click();
  const monthly = page.getByRole("dialog", { name: "Monthly management fees" });
  await monthly.getByLabel("Take last month's management fees by themselves").check();
  await monthly.getByLabel("From this day of the month").selectOption("1");
  await monthly.getByRole("button", { name: "Save" }).click();
  await expect(page.getByText("Saved the monthly management fees.")).toBeVisible();
  await expect(page.getByTestId("automatic-fees")).toHaveText(
    "Taken by themselves: last month's, from the 1st of each month",
  );
  await section(page, "Management fees");
  await expect(page.getByTestId("automatic-fees")).toHaveText(
    "Taken by themselves: last month's, from the 1st of each month.",
  );

  // Birch Flats' terms from this month: a $50.00 minimum and a $25.00 flat fee on top.
  await openOwner(page, "Demo Owner A");
  const terms = await setupPanel(page, "Set terms", "Set management terms");
  await terms.getByRole("combobox", { name: "For" }).selectOption({ label: "Demo Owner A, Birch Flats" });
  await terms.getByLabel("In force from").fill(firstOfThisMonth());
  await terms.getByLabel("Fee, percent of rent and recurring fees collected").fill("0");
  await terms.getByLabel("Minimum fee").fill("50.00");
  await terms.getByLabel("Flat fee").fill("25.00");
  // The saved toast sits over the panel's corner until it goes.
  await expect(page.getByTestId("toaster")).toHaveCount(0, { timeout: 10_000 });
  await terms.getByRole("button", { name: "Set terms" }).click();
  await expect(page.getByRole("main")).toContainText(
    `Birch Flats: 0% of rent collected, at least $50.00, plus $25.00; leasing fee 0%; reserve $0.00; the owner approves bills over $0.00; from ${us(firstOfThisMonth())}`,
  );
  await section(page, "Management fees");
  const nextMonth = new Date(Date.UTC(Number(today().slice(0, 4)), Number(today().slice(5, 7)), 1))
    .toISOString()
    .slice(0, 10);
  await page.getByLabel("Period from").fill(firstOfThisMonth());
  await page.getByLabel("Up to (not including)").fill(nextMonth);
  await expect(page.getByRole("row", { name: "Demo Owner A, Birch Flats", exact: true })).toContainText("$75.00");

  // An inspection fee from Birch Flats, which holds $250.00: on the owner's statement with what
  // it was for. More than the owner holds is refused, in words.
  await openOwner(page, "Demo Owner A");
  await page.getByRole("tab", { name: "Financials", exact: true }).click();
  const take = async (amount: string) => {
    await page.getByRole("button", { name: "Take a fee" }).click();
    const panel = page.getByRole("dialog", { name: "Take a fee" });
    await panel.getByLabel("From").selectOption({ label: "Birch Flats" });
    await panel.getByRole("combobox", { name: "Fee", exact: true }).selectOption({ label: "Inspection fee" });
    await panel.getByLabel("Amount").fill(amount);
    await panel.getByLabel("What it is for").fill("Move-in inspection");
    await expect(page.getByTestId("toaster")).toHaveCount(0, { timeout: 10_000 });
    await panel.getByRole("button", { name: "Take fee" }).click();
    return panel;
  };
  await take("40.00");
  await expect(page.getByText("A $40.00 inspection fee is taken from Birch Flats.")).toBeVisible();
  await expect(page.getByRole("main")).toContainText("Inspection fee: Move-in inspection");
  const refused = await take("500.00");
  await expect(refused.getByRole("alert")).toHaveText("not enough money: the owner's account would be $290.00 short");
  await refused.getByRole("button", { name: "Close" }).click();
  await expect((await ownerProperty(page, "Demo Owner A", "Birch Flats")).getByRole("cell").nth(2)).toHaveText("$210.00");
});

test("a company has tenants' prepaid rent pay their new rent by itself, and Run rent now uses it", async ({ page }) => {
  const people = seed.features["prepaid-rent"]!;
  await joinAsStaff(page, people.manager);

  // Company setup: off until the company turns it on.
  await settings(page, "Company setup");
  await expect(page.getByTestId("automatic-prepaid")).toHaveText("Used by hand with Use prepaid rent on the lease");
  await page.getByRole("button", { name: "Edit prepaid rent" }).click();
  const panel = page.getByRole("dialog", { name: "Prepaid rent" });
  await panel.getByLabel("Use tenants' prepaid rent by itself for new rent").check();
  await panel.getByRole("button", { name: "Save" }).click();
  await expect(page.getByText("Saved how prepaid rent is used.")).toBeVisible();
  await expect(page.getByTestId("automatic-prepaid")).toHaveText("Used by itself each night for what the lease owes");

  // Aspen Row Unit 1 owes nothing before this month's rent: $500.00 paid ahead is prepaid rent.
  const aspen = await openLease(page, "Aspen Row, Unit 1");
  await expect(page.getByTestId("balance-due")).toHaveText("$0.00");
  const pay = await leasePanel(page, "Receive payment");
  await pay.getByLabel("Payment received").fill("500.00");
  await pay.getByRole("button", { name: "Record payment" }).click();
  await expect(aspen).toContainText("$500.00 held as prepaid rent.");

  // Run rent now charges this month's $1,180.00 rent, then the prepaid rent pays $500.00 of it
  // (a rent past its five days' grace took its $50.00 late fee first).
  await section(page, "Rent roll");
  await page.getByRole("button", { name: "Actions for the rent roll" }).click();
  await page.getByRole("menuitem", { name: "Run rent now" }).click();
  const run = page.getByRole("dialog", { name: "Run rent now" });
  await run.getByLabel("Late fees as of").fill(today());
  await run.getByRole("button", { name: "Run rent now" }).click();
  await expect(run).toHaveCount(0);
  await expect(page.getByRole("main")).toContainText("Prepaid rent paid what 1 lease owed.");
  const late = dayOfMonth() > 6;
  await expect(page.getByRole("row", { name: "Aspen Row, Unit 1", exact: true }).getByTestId("balance")).toHaveText(
    late ? "$730.00" : "$680.00",
  );
});

test("staff share a roofer's invoice across two owners' properties with the markup Birch Flats' terms set, and pay both shares at once", async ({
  page,
}) => {
  const people = seed.features["shared-bills"]!;
  await joinAsStaff(page, people.manager);

  // Demo Owner A's terms for Birch Flats from this month let the company add 10% to a bill.
  await openOwner(page, "Demo Owner A");
  const terms = await setupPanel(page, "Set terms", "Set management terms");
  await terms.getByRole("combobox", { name: "For" }).selectOption({ label: "Demo Owner A, Birch Flats" });
  await terms.getByLabel("In force from").fill(firstOfThisMonth());
  await terms.getByLabel("Fee, percent of rent and recurring fees collected").fill("8");
  await terms.getByLabel("Minimum fee").fill("50.00");
  await terms.getByLabel("Reserve").fill("250.00");
  await terms.getByLabel("Owner approves bills over").fill("500.00");
  await terms.getByLabel("Markup on bills, percent").fill("10");
  await expect(page.getByTestId("toaster")).toHaveCount(0, { timeout: 10_000 });
  await terms.getByRole("button", { name: "Set terms" }).click();
  await expect(page.getByRole("main")).toContainText(
    `Birch Flats: 8% of rent collected, at least $50.00; leasing fee 0%; reserve $250.00; 10% markup on bills; the owner approves bills over $500.00; from ${us(firstOfThisMonth())}`,
  );

  // One roofer's invoice of $300.00 shared evenly by Birch Flats and Aspen Row (Demo Owner B,
  // whose terms set no markup): the markup goes on Birch Flats' share alone.
  await section(page, "Bills");
  await page.getByRole("button", { name: "Record bill" }).click();
  const panel = page.getByRole("dialog", { name: "Record bill" });
  await panel.getByRole("combobox", { name: "Vendor", exact: true }).selectOption({ label: "Demo Roofing Co" });
  await panel.getByLabel("Share across properties").check();
  await panel.getByRole("combobox", { name: "Owner and property, share 1" }).selectOption({ label: "Demo Owner A, Birch Flats" });
  await panel.getByRole("combobox", { name: "Owner and property, share 2" }).selectOption({ label: "Demo Owner B, Aspen Row" });
  await panel.getByLabel("Ref #").fill("ROOF-77");
  await panel.getByLabel("Invoice total").fill("300.00");
  await panel.getByRole("button", { name: "Split evenly" }).click();
  await expect(panel.getByTestId("shares-total")).toHaveText("Shares come to $300.00 of $300.00.");
  await panel.getByLabel("Memo").fill("Roof repair, both buildings");
  await panel.getByLabel("Add each owner's markup, as their terms set").check();
  await expect(page.getByTestId("toaster")).toHaveCount(0, { timeout: 10_000 });
  await panel.getByRole("button", { name: "Record bill" }).click();
  await expect(page.getByText("Bill ROOF-77 is shared across 2 properties.")).toBeVisible();
  await expect(panel).toHaveCount(0);

  // Each share is its owner's own bill; Birch Flats' carries the $15.00 markup.
  await billRow(page, "Demo Roofing Co", "ROOF-77/1").getByRole("cell").first().click();
  await expect(page.getByLabel("Summary")).toContainText("Markup$15.00 (10% in the owner's terms)");
  const shares = page.getByRole("table", { name: "Shares" });
  await expect(shares).toContainText("ROOF-77/1Demo Owner ABirch Flats$150.00");
  await expect(shares).toContainText("ROOF-77/2Demo Owner BAspen Row$150.00");

  // Pay bills pays both shares at once: Birch Flats gives its share and the markup.
  await section(page, "Bills");
  await page.getByRole("link", { name: "Pay bills" }).click();
  await page.getByLabel("Pay Demo Roofing Co ROOF-77/1").check();
  await page.getByLabel("Pay Demo Roofing Co ROOF-77/2").check();
  const cash = page.getByRole("table", { name: "Each property's cash" });
  await expect(cash.getByRole("row", { name: "Demo Owner A, Birch Flats" }).getByRole("cell")).toHaveText([
    "Demo Owner A, Birch Flats", "$250.00", "$165.00", "$85.00", "",
  ]);
  await page.getByRole("button", { name: "Pay $300.00" }).click();
  await page.getByRole("alertdialog").getByRole("button", { name: "Confirm" }).click();
  await expect(page.getByText("2 bills of $300.00 paid, by check.")).toBeVisible();
  await expect((await ownerProperty(page, "Demo Owner A", "Birch Flats")).getByRole("cell").nth(2)).toHaveText("$85.00");
  await expect((await ownerProperty(page, "Demo Owner B", "Aspen Row")).getByRole("cell").nth(2)).toHaveText("$50.00");
});

test("staff set Birch Flats' ownership 60/40 between two owners; a payment and a bill split between them to the cent", async ({
  page,
}) => {
  const people = seed.features.ownership!;
  await joinAsStaff(page, people.manager);

  // Demo Owner B comes in as a co-owner of Birch Flats; until shares are set, staff choose.
  await openProperty(page, "Birch Flats");
  const add = await setupPanel(page, "Add owner");
  await add.getByRole("combobox", { name: "Owner", exact: true }).selectOption({ label: "Demo Owner B" });
  await add.getByRole("button", { name: "Add owner" }).click();
  await expect(page.getByText("Added Demo Owner B's account.")).toBeVisible();
  const ownership = part(page, "Ownership");
  await expect(ownership).toContainText("No ownership shares.");

  const shares = await setupPanel(page, "Set ownership");
  await shares.getByLabel("In force from").fill(firstOfThisMonth());
  await shares.getByLabel("Demo Owner A, percent").fill("60");
  await shares.getByLabel("Demo Owner B, percent").fill("30");
  await expect(shares.getByTestId("ownership-total")).toHaveText("Together 90%; the shares come to 100%.");
  await shares.getByLabel("Demo Owner B, percent").fill("40");
  await expect(page.getByTestId("toaster")).toHaveCount(0, { timeout: 10_000 });
  await shares.getByRole("button", { name: "Set ownership" }).click();
  await expect(page.getByText(
    `Ownership of Birch Flats from ${us(firstOfThisMonth())}: Demo Owner A 60% · Demo Owner B 40%.`,
  )).toBeVisible();
  await expect(ownership.getByRole("table", { name: "Ownership" }).getByRole("row").nth(1).getByRole("cell")).toHaveText([
    us(firstOfThisMonth()), "Demo Owner A 60% · Demo Owner B 40%", /Demo/, "In force",
  ]);

  // A lost key's $100.01 paid: Demo Owner A's 60% is $60.006 and B's 40% $40.004, so the cent
  // left over goes to A, the larger remainder.
  await openLease(page, "Birch Flats, Unit A");
  const charge = await leasePanel(page, "Enter charge");
  await charge.getByLabel("Amount").fill("100.01");
  await charge.getByLabel("What for").fill("Lost key");
  await charge.getByRole("button", { name: "Add", exact: true }).click();
  await expect(page.getByText("Fee of $100.01 added.")).toBeVisible();
  const pay = await leasePanel(page, "Receive payment");
  await expect(pay.getByTestId("split-by-ownership")).toHaveText("Split by ownership: Demo Owner A 60% · Demo Owner B 40%");
  await pay.getByLabel("Payment received").fill("100.01");
  await expect(page.getByTestId("toaster")).toHaveCount(0, { timeout: 10_000 });
  await pay.getByRole("button", { name: "Record payment" }).click();
  await expect(page.getByText("$100.01 paid what is owed.")).toBeVisible();
  await expect((await ownerProperty(page, "Demo Owner B", "Birch Flats")).getByRole("cell").nth(2)).toHaveText("$40.00");

  // A plumber's $50.00 bill for Birch Flats is split the same way, a bill for each owner's share.
  await section(page, "Bills");
  await page.getByRole("button", { name: "Record bill" }).click();
  const bill = page.getByRole("dialog", { name: "Record bill" });
  await bill.getByRole("combobox", { name: "Vendor", exact: true }).selectOption({ label: "Demo Roofing Co" });
  await bill.getByRole("combobox", { name: "Owner and property" }).selectOption({ label: "Demo Owner A, Birch Flats" });
  await expect(bill.getByLabel("Split by ownership: Demo Owner A 60% · Demo Owner B 40%")).toBeChecked();
  await bill.getByLabel("Ref #").fill("GUTTER-5");
  await bill.getByLabel("Amount").fill("50.00");
  await bill.getByLabel("Memo").fill("Gutter cleaning");
  await expect(page.getByTestId("toaster")).toHaveCount(0, { timeout: 10_000 });
  await bill.getByRole("button", { name: "Record bill" }).click();
  await expect(page.getByText("Bill GUTTER-5 is split between 2 owners by their shares.")).toBeVisible();
  await billRow(page, "Demo Roofing Co", "GUTTER-5/1").getByRole("cell").first().click();
  const parts = page.getByRole("table", { name: "Shares" });
  await expect(parts).toContainText("Demo Owner ABirch Flats$30.00");
  await expect(parts).toContainText("Demo Owner BBirch Flats$20.00");

  // The same management terms for both owners (q044): one fee for Birch Flats, split 60/40. No
  // rent came in this month, so it is the $25.00 minimum: $15.00 and $10.00, not $25.00 each.
  await openProperty(page, "Birch Flats");
  const terms = await setupPanel(page, "Set terms", "Set management terms",
    page.getByRole("table", { name: "Owners" }).getByRole("row", { name: "Demo Owner B" }));
  await terms.getByRole("combobox", { name: "For" }).selectOption({ label: "Every owner of Birch Flats (one fee, split by their shares)" });
  await terms.getByLabel("In force from").fill(firstOfThisMonth());
  await terms.getByLabel("Fee, percent of rent and recurring fees collected").fill("8");
  await terms.getByLabel("Minimum fee").fill("25.00");
  await expect(page.getByTestId("toaster")).toHaveCount(0, { timeout: 10_000 });
  await terms.getByRole("button", { name: "Set terms" }).click();
  await expect(page.getByText(`New terms for every owner of Birch Flats from ${firstOfThisMonth()}.`)).toBeVisible();
  await section(page, "Management fees");
  const nextMonth = new Date(Date.UTC(Number(today().slice(0, 4)), Number(today().slice(5, 7)), 1))
    .toISOString()
    .slice(0, 10);
  await page.getByLabel("Period from").fill(firstOfThisMonth());
  await page.getByLabel("Up to (not including)").fill(nextMonth);
  await expect(page.getByRole("row", { name: "Demo Owner A, Birch Flats", exact: true })).toContainText("$15.00");
  await expect(page.getByRole("row", { name: "Demo Owner B, Birch Flats", exact: true })).toContainText("$10.00");
});

test("staff add a housing authority, set a lease's assistance, charge and credit the authority and record its payment; its late fee, a bounce, and a payment past a hold", async ({
  page,
}) => {
  const people = seed.features["housing-assistance"]!;
  await joinAsStaff(page, people.manager);

  await section(page, "Housing authorities");
  await expect(page.getByText("No housing authorities yet")).toBeVisible();
  await page.getByRole("button", { name: "Add housing authority" }).click();
  const add = page.getByRole("dialog", { name: "Add housing authority" });
  await add.getByLabel("Housing authority's name").fill("Demo Housing Authority");
  await add.getByRole("button", { name: "Add housing authority" }).click();
  await expect(page.getByRole("heading", { level: 1, name: "Demo Housing Authority" })).toBeVisible();
  await expect(page.getByText("No leases yet")).toBeVisible();

  // From next month the authority pays $800.00 of each month's rent; this month is charged already.
  await openLease(page, "Birch Flats, Unit A");
  const card = part(page, "Housing assistance");
  await expect(card.getByTestId("assistance-split")).toHaveText(/^The tenant pays all of the \$[\d,]+\.\d\d rent\.$/);
  await card.getByRole("button", { name: "Set housing assistance" }).click();
  const terms = page.getByRole("dialog", { name: "Set housing assistance" });
  const from = await terms.getByLabel("From (the first of a month)").inputValue();
  await terms.getByLabel("The authority pays each month").fill("800.00");
  await terms.getByLabel("HAP contract (optional)").fill("HAP-DEMO-1");
  await expect(page.getByTestId("toaster")).toHaveCount(0, { timeout: 10_000 });
  await terms.getByRole("button", { name: "Save assistance" }).click();
  await expect(page.getByText(`The housing authority pays $800.00 a month from ${us(from)}.`)).toBeVisible();
  await expect(card.getByRole("list", { name: "Housing assistance, Unit A" })).toHaveText(
    `From ${us(from)}: Demo Housing Authority pays $800.00 a month (contract HAP-DEMO-1)`,
  );

  // This month's part, before the contract was on file, charged by hand; an abatement off it.
  await card.getByRole("button", { name: "Charge the authority" }).click();
  const charge = page.getByRole("dialog", { name: "Charge the housing authority" });
  await charge.getByLabel("Amount").fill("800.00");
  await charge.getByLabel("Due on").fill(firstOfThisMonth());
  await charge.getByLabel("What it is for").fill("This month's part");
  await expect(page.getByTestId("toaster")).toHaveCount(0, { timeout: 10_000 });
  await charge.getByRole("button", { name: "Charge the authority" }).click();
  await expect(page.getByText("$800.00 charged to the housing authority.")).toBeVisible();
  await card.getByRole("button", { name: "Actions for This month's part" }).click();
  await page.getByRole("menuitem", { name: "Credit the authority" }).click();
  const credit = page.getByRole("dialog", { name: "Credit the housing authority" });
  await credit.getByLabel("Amount").fill("50.00");
  await credit.getByLabel("Why").fill("Abatement");
  await expect(page.getByTestId("toaster")).toHaveCount(0, { timeout: 10_000 });
  await credit.getByRole("button", { name: "Credit the authority" }).click();
  await expect(page.getByText("$50.00 credited off This month's part.")).toBeVisible();
  await expect(card.getByTestId("authority-owes")).toHaveText("The housing authority owes $750.00 for this lease.");

  // Its payment: what it owes on the lease, into the owner's account.
  await section(page, "Housing authorities");
  await expect(page.getByRole("row", { name: "Demo Housing Authority" }).getByRole("cell")).toHaveText([
    "Demo Housing Authority", "0", "$750.00",
  ]);
  await page.getByRole("row", { name: "Demo Housing Authority" }).click();
  await page.getByRole("button", { name: "Record payment" }).click();
  const pay = page.getByRole("dialog", { name: "Record payment" });
  await pay.getByRole("button", { name: "Fill in what it owes" }).click();
  await expect(pay.getByTestId("authority-pay-total")).toHaveText("Total $750.00");
  await pay.getByLabel("Check or reference").fill("HAP 0001");
  await expect(page.getByTestId("toaster")).toHaveCount(0, { timeout: 10_000 });
  await pay.getByRole("button", { name: "Record payment" }).click();
  await expect(page.getByText("$750.00 from Demo Housing Authority recorded.")).toBeVisible();
  await expect(page.getByTestId("authority-owed")).toHaveText("Owes $0.00 in all.");
  await page.getByRole("table", { name: "Leases" }).getByRole("link", { name: /Birch Flats Unit A$/ }).click();
  await expect(part(page, "Housing assistance").getByRole("row", { name: /^This month's part/ }).getByRole("cell")).toHaveText([
    us(firstOfThisMonth()), "This month's part", "$750.00", "$750.00", "$0.00", /.*/,
  ]);

  // Its late payment fee, as its HAP contract provides (q034): charged on the authority, never
  // the tenant, by the run once its part is still open after the grace days.
  await section(page, "Housing authorities");
  await page.getByRole("row", { name: "Demo Housing Authority" }).click();
  await page.getByRole("button", { name: "Set late fee" }).click();
  const fee = page.getByRole("dialog", { name: "Set late fee" });
  await fee.getByLabel("In force from").fill(firstOfThisMonth());
  await fee.getByLabel("Grace days").fill("5");
  await fee.getByLabel("Late payment fee").fill("25.00");
  await expect(page.getByTestId("toaster")).toHaveCount(0, { timeout: 10_000 });
  await fee.getByRole("button", { name: "Set late fee" }).click();
  await expect(page.getByTestId("authority-late-fee")).toHaveText(
    `A late payment fee of $25.00 when its part of a rent is still open 5 days after it was due (from ${us(firstOfThisMonth())}).`,
  );

  // The bank returns its payment: the money moves back and the authority owes it again.
  const payments = page.getByRole("table", { name: "Payments" });
  await payments.getByRole("button", { name: /^Actions for .* payment of \$750\.00$/ }).click();
  await page.getByRole("menuitem", { name: "It bounced" }).click();
  await page.getByRole("alertdialog").getByRole("button", { name: "It bounced" }).click();
  await expect(page.getByText("The $750.00 payment bounced; the authority owes it again.")).toBeVisible();
  await expect(page.getByTestId("authority-owed")).toHaveText("Owes $750.00 in all.");
  await expect(payments).toContainText("Bounced");

  // An eviction filed on the lease: the authority's payment is taken only past the hold.
  await openLease(page, "Birch Flats, Unit A");
  const hold = await leasePanel(page, "Place payment hold");
  await hold.getByLabel("Hold payments from").fill(firstOfThisMonth());
  await hold.getByRole("button", { name: "Place hold" }).click();
  await expect(page.getByText(`On a payment hold since ${us(firstOfThisMonth())}.`)).toBeVisible();
  await section(page, "Housing authorities");
  await page.getByRole("row", { name: "Demo Housing Authority" }).click();
  await expect(page.getByRole("table", { name: "Leases" })).toContainText(
    `On a payment hold since ${us(firstOfThisMonth())}`,
  );
  await page.getByRole("button", { name: "Record payment" }).click();
  const past = page.getByRole("dialog", { name: "Record payment" });
  await past.getByRole("button", { name: "Fill in what it owes" }).click();
  await past.getByLabel("Accept this payment past the payment hold").check();
  await expect(page.getByTestId("toaster")).toHaveCount(0, { timeout: 10_000 });
  await past.getByRole("button", { name: "Record payment" }).click();
  await expect(page.getByTestId("authority-owed")).toHaveText("Owes $0.00 in all.");
});

test("staff record an assisted household's annual certification and a failed inspection, and the compliance report says where each stands", async ({
  page,
}) => {
  const people = seed.features["assistance-compliance"]!;
  await joinAsStaff(page, people.manager);
  await section(page, "Housing authorities");
  await page.getByRole("button", { name: "Add housing authority" }).click();
  const add = page.getByRole("dialog", { name: "Add housing authority" });
  await add.getByLabel("Housing authority's name").fill("Demo Housing Authority");
  await add.getByRole("button", { name: "Add housing authority" }).click();
  await expect(page.getByRole("heading", { level: 1, name: "Demo Housing Authority" })).toBeVisible();

  // Assistance from next month; nothing to certify or inspect for until it is on file.
  await openLease(page, "Birch Flats, Unit A");
  const card = part(page, "Housing assistance");
  await expect(card.getByRole("button", { name: "Record certification" })).toHaveCount(0);
  await card.getByRole("button", { name: "Set housing assistance" }).click();
  const terms = page.getByRole("dialog", { name: "Set housing assistance" });
  const from = await terms.getByLabel("From (the first of a month)").inputValue();
  await terms.getByLabel("The authority pays each month").fill("800.00");
  await expect(page.getByTestId("toaster")).toHaveCount(0, { timeout: 10_000 });
  await terms.getByRole("button", { name: "Save assistance" }).click();
  await expect(page.getByText(`The housing authority pays $800.00 a month from ${us(from)}.`)).toBeVisible();
  await expect(card.getByTestId("recertification")).toHaveText("none on fileNo income certification on file");

  // This year's certification, due again a year on.
  await card.getByRole("button", { name: "Record certification" }).click();
  const certification = page.getByRole("dialog", { name: "Record an income certification" });
  await certification.getByLabel("Kind").selectOption("annual");
  await certification.getByLabel("Takes effect").fill(today());
  await certification.getByLabel("People in the household").fill("3");
  await certification.getByLabel("Annual income").fill("24000.00");
  await expect(page.getByTestId("toaster")).toHaveCount(0, { timeout: 10_000 });
  await certification.getByRole("button", { name: "Record certification" }).click();
  await expect(page.getByText(`Annual certification from ${us(today())} recorded.`)).toBeVisible();
  const due = `${Number(today().slice(0, 4)) + 1}${today().slice(4)}`.replace(/-02-29$/, "-02-28");
  await expect(card.getByTestId("recertification")).toHaveText(`currentRecertify by ${us(due)}`);

  // Today's inspection failed; the home must pass a reinspection within 45 days.
  await card.getByRole("button", { name: "Record inspection" }).click();
  const inspection = page.getByRole("dialog", { name: "Record an inspection" });
  await inspection.getByLabel("Inspected on").fill(today());
  await inspection.getByLabel("Result").selectOption("failed");
  await inspection.getByLabel("Must pass a reinspection by").fill(daysFromToday(45));
  await expect(page.getByTestId("toaster")).toHaveCount(0, { timeout: 10_000 });
  await inspection.getByRole("button", { name: "Record inspection" }).click();
  await expect(page.getByText(`Inspection on ${us(today())} recorded: failed.`)).toBeVisible();
  await expect(card.getByTestId("inspection")).toHaveText(`failed, reinspection dueInspect by ${us(daysFromToday(45))}`);
  await expect(card.getByRole("table", { name: "Inspections" })).toContainText("Demo Housing Authority");

  // The report, as of the first day of the assistance: days and words, never the household's income.
  await section(page, "Reports");
  await page.getByRole("list", { name: "Compliance" }).getByRole("link", { name: "Housing assistance compliance" }).click();
  await page.getByLabel("As of").fill(from);
  const report = page.getByRole("table", { name: "Housing assistance compliance" });
  const row = report.getByRole("row").filter({ hasText: "Unit A" });
  await expect(row).toContainText(`annual ${us(today())}`);
  await expect(row).toContainText("failed, reinspection due");
  await expect(report).toContainText("0 to certify now, 0 within 120 days");
  await expect(report).not.toContainText("24,000");
  const href = (await page.getByRole("link", { name: "CSV", exact: true }).getAttribute("href")) ?? "";
  const csv = await (await page.request.get(href)).text();
  expect(csv).toContain(`${due},current,failed ${us(today())},${daysFromToday(45)},"failed, reinspection due"`);
  expect(csv).not.toContain("24000");
});

test("staff add Cedar House's street address, find it on a map, find it by its street, and a new address is found again", async ({
  page,
}) => {
  const people = seed.features["property-addresses"]!;
  await joinAsStaff(page, people.manager);
  await section(page, "Properties");
  await page.getByRole("row", { name: "Cedar House", exact: true }).getByRole("link", { name: "Cedar House" }).click();
  await expect(page.getByRole("heading", { level: 1, name: "Cedar House" })).toBeVisible();
  const card = part(page, "Address");
  await expect(card).toContainText("No address yet.");

  await card.getByRole("button", { name: "Add address" }).click();
  const add = page.getByRole("dialog", { name: "Add address" });
  await add.getByLabel("Street").fill("100 Synthetic Way");
  await add.getByLabel("City").fill("Demo City");
  await add.getByLabel("State (two letters)").fill("ca");
  await add.getByLabel("ZIP code").fill("00001");
  await expect(page.getByTestId("toaster")).toHaveCount(0, { timeout: 10_000 });
  await add.getByRole("button", { name: "Save address" }).click();
  await expect(page.getByText("Address saved.")).toBeVisible();
  await expect(card.getByTestId("property-address")).toHaveText("100 Synthetic WayDemo City, CA 00001");
  await expect(card).toContainText("Not found on a map yet.");

  // The fake geocoder calls nothing; the link opens OpenStreetMap at its point.
  await card.getByRole("button", { name: "Find on map" }).click();
  await expect(page.getByText("Found on the map.")).toBeVisible();
  await expect(card.getByTestId("property-location")).toContainText("as the geocoder matched it: 100 SYNTHETIC WAY, DEMO CITY, CA 00001.");
  await expect(card.getByRole("link", { name: "Open in OpenStreetMap" })).toHaveAttribute(
    "href", /^https:\/\/www\.openstreetmap\.org\/\?mlat=-?\d+\.\d{6}&mlon=-?\d+\.\d{6}#map=17\//,
  );

  // The list shows it, and the search bar finds the property by its street.
  await section(page, "Properties");
  await expect(page.getByRole("row", { name: "Cedar House", exact: true })).toContainText("100 Synthetic Way, Demo City, CA 00001");
  const search = page.getByLabel("Search people, homes, bills, work orders and files");
  await search.fill("synthetic way");
  await expect(page.getByRole("list", { name: "Search results" })).toContainText("100 Synthetic Way, Demo City, CA 00001");
  await page.getByRole("list", { name: "Search results" }).getByRole("link", { name: /Cedar House/ }).click();
  await expect(page.getByRole("heading", { level: 1, name: "Cedar House" })).toBeVisible();

  // A new address isn't where the old one was until it is found.
  await card.getByRole("button", { name: "Edit address" }).click();
  const edit = page.getByRole("dialog", { name: "Edit address" });
  await edit.getByLabel("Street").fill("200 Example Street");
  await edit.getByLabel("Building or unit (optional)").fill("Building B");
  await expect(page.getByTestId("toaster")).toHaveCount(0, { timeout: 10_000 });
  await edit.getByRole("button", { name: "Save address" }).click();
  await expect(card.getByTestId("property-address")).toHaveText("200 Example StreetBuilding BDemo City, CA 00001");
  await expect(card.getByTestId("property-location")).toHaveCount(0);
  await expect(card).toContainText("Not found on a map yet.");
});

test("staff archive a vendor with nothing open, who leaves the list, the work order picker and search, keep one with a bill not paid, and bring the first back", async ({
  page,
}) => {
  const people = seed.features["vendor-archive"]!;
  await joinAsStaff(page, people.manager);
  await section(page, "Vendors");
  await page.getByRole("button", { name: "Add vendor" }).click();
  const adding = page.getByRole("dialog", { name: "Add vendor" });
  await adding.getByLabel("Vendor's name").fill("Demo Painting Co");
  await adding.getByRole("button", { name: "Add vendor" }).click();
  await expect(page.getByRole("heading", { level: 1, name: "Demo Painting Co" })).toBeVisible();

  // Nothing open with them: archived, after a question.
  await chooseAction(page, "Demo Painting Co", "Archive");
  await expect(page.getByText("Archived", { exact: true })).toBeVisible();
  await section(page, "Vendors");
  await expect(page.getByRole("row", { name: "Demo Painting Co", exact: true })).toHaveCount(0);
  await page.getByRole("combobox", { name: "Status" }).selectOption("archived");
  await expect(page.getByRole("row", { name: "Demo Painting Co", exact: true })).toBeVisible();
  const search = page.getByLabel("Search people, homes, bills, work orders and files");
  await search.fill("painting");
  await expect(page.getByRole("list", { name: "Search results" })).toHaveCount(0);
  await search.fill("");

  // Work orders don't offer them.
  await section(page, "Work orders");
  const vendors = page.getByRole("combobox", { name: "Vendor", exact: true });
  await expect(vendors.locator("option", { hasText: "Demo Plumbing Co" })).toHaveCount(1);
  await expect(vendors.locator("option", { hasText: "Demo Painting Co" })).toHaveCount(0);

  // A vendor with a bill not paid stays, and the refusal says why in words.
  await section(page, "Bills");
  await recordBill(page, "Demo Plumbing Co", "Demo Owner A, Birch Flats", "PLB-9001", "120.00", "Drain");
  await section(page, "Vendors");
  await page.getByRole("row", { name: "Demo Plumbing Co", exact: true }).getByRole("link").click();
  await chooseAction(page, "Demo Plumbing Co", "Archive");
  await expect(page.getByRole("alert")).toHaveText(
    "Demo Plumbing Co has a bill not paid; pay or decline it before archiving",
  );

  // Brought back, they are on the list again.
  await section(page, "Vendors");
  await page.getByRole("combobox", { name: "Status" }).selectOption("archived");
  await page.getByRole("row", { name: "Demo Painting Co", exact: true }).getByRole("link").click();
  await chooseAction(page, "Demo Painting Co", "Bring back");
  await expect(page.getByText("Archived", { exact: true })).toHaveCount(0);
  await section(page, "Vendors");
  await expect(page.getByRole("row", { name: "Demo Painting Co", exact: true })).toBeVisible();
});

test("staff bill last month's water back to Birch Flats' leases by unit, the owner keeping 10% and what isn't billed", async ({
  page,
}) => {
  const people = seed.features["utility-billing"]!;
  await joinAsStaff(page, people.manager);
  const from = firstOfMonthsAgo(1);
  const to = new Date(Date.parse(`${firstOfThisMonth()}T00:00:00Z`) - 86_400_000).toISOString().slice(0, 10);

  await section(page, "Utility billing");
  await page.getByRole("combobox", { name: "Property", exact: true }).selectOption({ label: "Birch Flats" });
  await expect(page.getByText("No utility is billed back yet.")).toBeVisible();

  // Water by unit from last month, the owner keeping 10% for the common areas.
  await page.getByRole("button", { name: "Set plan" }).click();
  const plan = page.getByRole("dialog", { name: "Set plan" });
  await plan.getByLabel("From").fill(from);
  await plan.getByLabel("Owner keeps (percent, for the common areas)").fill("10");
  await expect(page.getByTestId("toaster")).toHaveCount(0, { timeout: 10_000 });
  await plan.getByRole("button", { name: "Save plan" }).click();
  await expect(page.getByText(`Water is billed back by unit from ${us(from)}.`)).toBeVisible();

  // Unit A's lease says it pays water; Unit B's doesn't.
  await page.getByRole("button", { name: "Actions for Morgan Demo, Birch Flats Unit A" }).click();
  await page.getByRole("menuitem", { name: "Utilities billed" }).click();
  const billed = page.getByRole("dialog", { name: "Utilities billed" });
  await billed.getByLabel("From").fill(from);
  await expect(page.getByTestId("toaster")).toHaveCount(0, { timeout: 10_000 });
  await billed.getByRole("button", { name: "Save" }).click();
  await expect(page.getByText(`Morgan Demo, Birch Flats Unit A is billed for water from ${us(from)}.`)).toBeVisible();

  await page.getByRole("button", { name: "Actions for Unit A" }).click();
  await page.getByRole("menuitem", { name: "Set square feet" }).click();
  const area = page.getByRole("dialog", { name: "Square feet" });
  await area.getByLabel("Square feet, Unit A").fill("750");
  await expect(page.getByTestId("toaster")).toHaveCount(0, { timeout: 10_000 });
  await area.getByRole("button", { name: "Save" }).click();
  await expect(page.getByText("Unit A: 750 square feet.")).toBeVisible();

  // Last month's $90.00 bill: 10% off leaves $81.00, $40.50 a unit; Unit B's isn't billed.
  await page.getByRole("button", { name: "Bill back a utility" }).click();
  const bill = page.getByRole("dialog", { name: "Bill back a utility" });
  await bill.getByLabel("Period from").fill(from);
  await bill.getByLabel("Period to").fill(to);
  await bill.getByLabel("The bill's amount").fill("90.00");
  await bill.getByRole("button", { name: "Preview" }).click();
  await expect(bill.getByTestId("bill-preview")).toContainText(
    "By unit, the owner keeping 10%: $40.50 to the leases, $49.50 stays with the owner.",
  );
  await expect(page.getByTestId("toaster")).toHaveCount(0, { timeout: 10_000 });
  await bill.getByRole("button", { name: "Charge the leases" }).click();
  await expect(page.getByText("Water billed back: $40.50 charged to the leases; the owner keeps $49.50.")).toBeVisible();
  const entered = page.getByRole("listitem", { name: `Water, ${us(from)} to ${us(to)}` });
  await expect(entered.getByRole("row", { name: "Jamie Sample, Birch Flats Unit B" }).getByRole("cell")).toHaveText([
    "Jamie Sample, Birch Flats Unit B", /days$/, "Not billed",
  ]);
  await expect(entered.getByRole("row", { name: "Morgan Demo, Birch Flats Unit A" }).getByRole("cell").last()).toHaveText("$40.50");
});

test("staff go on call; a tenant files an emergency with a photo and staff see it through to a visit the tenant sees, day and window", async ({
  browser,
  page,
}) => {
  const people = seed.features["emergencies"]!;
  await joinAsStaff(page, people.manager);
  await section(page, "Work orders");
  await page.getByRole("button", { name: "On call" }).click();
  const onCall = page.getByRole("dialog", { name: "On call" });
  await expect(onCall.getByRole("list", { name: "On call" })).toContainText("Demo Bookkeeper(no login to email)");
  await onCall.getByLabel("Demo Manager", { exact: true }).check();
  await expect(onCall).toContainText("Demo Manager is on call.");
  await onCall.getByRole("button", { name: "Close" }).click();

  // The tenant files an emergency with a photo; they don't want anyone going in unannounced.
  const tenant = await browser.newPage();
  await joinPortal(tenant, people.taylor);
  const requests = part(tenant, "Maintenance requests");
  await requests.getByLabel("What needs fixing?").fill("No heat in the bedroom");
  await requests.getByLabel("This is an emergency (no heat, no water, a leak, a gas smell)").check();
  await requests
    .getByLabel("Photos (up to 3, JPEG, PNG or PDF)")
    .setInputFiles({ name: "radiator.png", mimeType: "image/png", buffer: LOGO });
  await expect(requests.getByTestId("photos-chosen")).toHaveText("Photos: radiator");
  await requests.getByRole("button", { name: "Send request" }).click();
  await expect(tenant.getByText(/as an emergency\. If anyone is in danger, call 911\./)).toBeVisible();
  const filed = requests.getByRole("listitem").filter({ hasText: "No heat in the bedroom" });
  await expect(filed).toContainText("Emergency");
  await expect(filed).toContainText("1 photo.");

  // Staff: the request is marked, its work order opens urgent with the tenant's word on entry.
  await page.reload();
  const request = page.getByRole("listitem", { name: "No heat in the bedroom" });
  await expect(request).toContainText("Emergency");
  await expect(request).toContainText("1 photo, on its work order.");
  await request.getByRole("button", { name: "Open work order" }).click();
  await request.getByRole("link", { name: "Open its work order" }).click();
  await expect(page.getByTestId("priority")).toHaveText("Urgent");
  await expect(page.getByLabel("Summary")).toContainText("Call the tenant before going in");
  await expect(page.getByLabel("Summary")).toContainText("Days open0 days");
  await expect(page.getByTestId("request")).toContainText("Emergency");
  await expect(page.getByTestId("emailed")).toHaveText("Emailed to who was on call: Demo Manager.");
  const photo = page.getByRole("list", { name: "Request photos" }).getByRole("link", { name: "radiator" });
  const file = await page.request.get((await photo.getAttribute("href")) ?? "");
  expect(file.headers()["content-type"]).toBe("image/png");

  // A vendor, then the visit: a day and a window.
  const visit = daysFromToday(2);
  await page.getByRole("button", { name: "Next step" }).click();
  let next = page.getByRole("dialog", { name: "Next step" });
  await next.getByRole("combobox", { name: "Vendor", exact: true }).selectOption({ label: "Demo Plumbing Co" });
  await next.getByRole("button", { name: "Record" }).click();
  await expect(next).toHaveCount(0);
  await page.getByRole("button", { name: "Next step" }).click();
  next = page.getByRole("dialog", { name: "Next step" });
  await next.getByLabel("Visit on").fill(visit);
  await next.getByLabel("Window (optional, like 8-10 am)").fill("8-10 am");
  await next.getByRole("button", { name: "Record" }).click();
  await expect(next).toHaveCount(0);
  await expect(page.getByLabel("Summary")).toContainText(`Scheduled for${us(visit)}, 8-10 am`);
  await expect(page.getByRole("list", { name: "Timeline" })).toContainText(`Scheduled for ${us(visit)}, 8-10 am`);

  // The tenant sees the day and the window.
  await tenant.reload();
  await expect(part(tenant, "Maintenance requests").getByTestId("visit")).toHaveText(`Visit on ${us(visit)}, 8-10 am`);
  await tenant.close();

  // The list counts each work order's days open.
  await section(page, "Work orders");
  const row = page.getByRole("row", { name: "No heat in the bedroom" });
  await expect(row.getByRole("cell").nth(7)).toHaveText("0");
});

test("staff add a filter change every 3 months, the ones due open at once, stop it, and charge the tenant for a work order", async ({
  page,
}) => {
  const people = seed.features["recurring-work-orders"]!;
  await joinAsStaff(page, people.manager);
  await section(page, "Work orders");
  await page.getByRole("button", { name: "Recurring" }).click();
  const panel = page.getByRole("dialog", { name: "Recurring work orders" });
  await expect(panel).toContainText("No work order comes back by itself yet.");
  await panel.getByRole("combobox", { name: "Property", exact: true }).selectOption({ label: "Cedar House" });
  await panel.getByRole("combobox", { name: "Unit", exact: true }).selectOption({ label: "House" });
  await panel.getByLabel("What it is").fill("Change the furnace filter");
  await panel.getByRole("combobox", { name: "Every", exact: true }).selectOption({ label: "3 months" });
  await panel.getByLabel("On day (1 to 28)").fill("1");
  // From five months ago: the 1st five months ago and two months ago are due already.
  await panel.getByLabel("From").fill(firstOfMonthsAgo(5));
  await panel.getByRole("combobox", { name: "Priority", exact: true }).selectOption({ label: "High" });
  await panel.getByRole("button", { name: "Add recurring work order" }).click();
  await expect(panel).toContainText('"Change the furnace filter" comes back by itself. 2 already due opened now.');
  const item = panel.getByRole("listitem", { name: "Change the furnace filter" });
  await expect(item).toContainText(`Change the furnace filter, House, Cedar House: every 3 months on the 1st from ${us(firstOfMonthsAgo(5))}`);
  await expect(item).toContainText(`High priority, no vendor. Last opened ${us(firstOfMonthsAgo(2))}.`);
  await item.getByLabel('Stop "Change the furnace filter" from').fill(today());
  await item.getByRole("button", { name: "Stop it" }).click();
  await expect(panel).toContainText(`"Change the furnace filter" stops from ${us(today())}.`);
  await expect(item).toContainText(`Ends ${us(today())}.`);
  await panel.getByRole("button", { name: "Close" }).click();

  // Both opened, high priority; the newer one first.
  await page.reload();
  const opened = page.getByRole("row", { name: "Change the furnace filter" });
  await expect(opened).toHaveCount(2);
  await opened.first().click();
  await expect(page.getByTestId("priority")).toHaveText("High");
  await expect(page.getByLabel("Summary")).toContainText("RecurringOpened by itself, every 3 months");
  await expect(page.getByLabel("Summary")).toContainText(`Opened${us(firstOfMonthsAgo(2))}`);

  // The tenant left the filter housing broken: they are charged for it.
  await page.getByRole("button", { name: "Charge the tenant" }).click();
  const charge = page.getByRole("dialog", { name: "Charge the tenant" });
  await expect(charge.getByRole("combobox", { name: "Lease", exact: true })).toContainText("House: Quinn Demo, Taylor Demo");
  await charge.getByLabel("Amount").fill("85.00");
  await charge.getByLabel("Memo").fill("Broken filter housing");
  await charge.getByRole("button", { name: "Charge" }).click();
  await expect(charge).toHaveCount(0);
  await expect(page.getByText("$85.00 charged to Quinn Demo, Taylor Demo.")).toBeVisible();
  const charged = page.getByRole("table", { name: "Charged to tenants" });
  await expect(charged.getByRole("cell")).toHaveText([
    "House", "Quinn Demo, Taylor Demo", us(today()), "Broken filter housing", "$85.00", "$85.00",
  ]);
});

test("staff send a plumber their link; the plumber schedules the visit and says it is done; staff take the link back", async ({
  browser,
  page,
}) => {
  const people = seed.features["vendor-links"]!;
  await joinAsStaff(page, people.manager);
  // A work order for the plumber.
  await section(page, "Work orders");
  await page.getByRole("button", { name: "New work order" }).click();
  const opening = page.getByRole("dialog", { name: "New work order" });
  await opening.getByRole("combobox", { name: "Property", exact: true }).selectOption({ label: "Cedar House" });
  await opening.getByLabel("Title").fill("Kitchen sink drains slowly");
  await opening.getByRole("button", { name: "Open work order" }).click();
  await expect(page.getByRole("heading", { level: 1, name: /Kitchen sink drains slowly$/ })).toBeVisible();
  const orderUrl = page.url();
  await page.getByRole("button", { name: "Next step" }).click();
  const next = page.getByRole("dialog", { name: "Next step" });
  await next.getByRole("combobox", { name: "Vendor", exact: true }).selectOption({ label: "Demo Plumbing Co" });
  await next.getByRole("button", { name: "Record" }).click();
  await expect(next).toHaveCount(0);

  // The plumber's link, from their page; shown once.
  await section(page, "Vendors");
  await page.getByRole("row", { name: "Demo Plumbing Co", exact: true }).click();
  await page.getByRole("tab", { name: "Work orders" }).click();
  await expect(page.getByTestId("vendor-link")).toContainText("Demo Plumbing Co has no link.");
  await page.getByRole("button", { name: "Make a link" }).click();
  const link = await page.getByLabel("Work order link, Demo Plumbing Co").inputValue();
  expect(link).toMatch(/\/vendor#.{20,}$/);

  // The plumber, with nothing but the link.
  const plumber = await browser.newPage();
  await plumber.goto(new URL(link).pathname + new URL(link).hash);
  await expect(plumber.getByRole("heading", { name: /^Demo Plumbing Co: work orders from / })).toBeVisible();
  const job = plumber.getByRole("region", { name: "Kitchen sink drains slowly" });
  const visit = daysFromToday(1);
  await job.getByLabel("Visit on").fill(visit);
  await job.getByLabel("Window (optional)").fill("1-3 pm");
  await job.getByRole("button", { name: "Schedule the visit" }).click();
  await expect(job.getByTestId("visit")).toHaveText(`Visit on ${us(visit)}, 1-3 pm`);

  // Staff see the visit as the plumber's own step.
  await page.goto(orderUrl);
  await expect(page.getByRole("list", { name: "Timeline" })).toContainText(`Scheduled for ${us(visit)}, 1-3 pm (by the vendor)`);

  await job.getByLabel("What you did (optional)").fill("Cleared the trap");
  await job.getByRole("button", { name: "The work is done" }).click();
  await expect(plumber.getByText(/is done\. Thank you\./)).toBeVisible();
  await expect(plumber.getByRole("region", { name: "Kitchen sink drains slowly" })).toHaveCount(0);
  await page.reload();
  await expect(page.getByTestId("status")).toHaveText("Completed");
  await expect(page.getByRole("list", { name: "Timeline" })).toContainText("Completed (by the vendor): Cleared the trap");

  // Taken back, the link opens nothing.
  await section(page, "Vendors");
  await page.getByRole("row", { name: "Demo Plumbing Co", exact: true }).click();
  await page.getByRole("tab", { name: "Work orders" }).click();
  await page.getByRole("button", { name: "Take back" }).click();
  await page.getByRole("alertdialog").getByRole("button", { name: "Take back" }).click();
  await expect(page.getByTestId("vendor-link")).toContainText("Demo Plumbing Co has no link.");
  await plumber.reload();
  await expect(plumber.getByRole("alert")).toHaveText("this link was taken back or doesn't exist");
  await plumber.close();
});

test("staff add photos to a listed unit, which its public listing shows; a removed one leaves it", async ({
  browser,
  page,
}) => {
  const people = seed.features["listing-photos"]!;
  // A real 1x1 PNG, so the browser decodes what the public page serves.
  const pixel = Buffer.from(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg==",
    "base64",
  );
  await joinAsStaff(page, people.manager);
  await section(page, "Listings");
  const unit = page.getByRole("row", { name: "Aspen Row, Unit 2" });
  await unit.getByRole("button", { name: "List", exact: true }).click();
  const listing = page.getByRole("dialog", { name: "List on the public page" });
  await listing.getByLabel("Rent a month").fill("1240.00");
  await listing.getByRole("button", { name: "List it" }).click();
  await expect(page.getByText("Listed Aspen Row, Unit 2 at $1,240.00.")).toBeVisible();
  await expect(unit.getByRole("cell").nth(5)).toHaveText("1 day");

  // Two photos from the unit's row.
  await unit.getByRole("button", { name: "Photos, Aspen Row, Unit 2" }).click();
  const photos = page.getByRole("dialog", { name: "Listing photos" });
  await expect(photos).toContainText("No photo yet.");
  const picker = photos.getByLabel("Add a photo (JPEG or PNG)");
  await picker.setInputFiles({ name: "Kitchen.png", mimeType: "image/png", buffer: pixel });
  await expect(photos.getByText("Added Kitchen.")).toBeVisible();
  await picker.setInputFiles({ name: "Porch.png", mimeType: "image/png", buffer: pixel });
  await expect(photos.getByText("Added Porch.")).toBeVisible();
  await expect(photos.getByRole("img")).toHaveCount(2);
  await photos.getByRole("button", { name: "Close" }).click();
  await expect(unit.getByRole("cell").nth(6)).toHaveText("2");

  // Anyone sees them on the public page, in the order they were added.
  const visitor = await (await browser.newContext()).newPage();
  await visitor.goto(`/listings?pmc=${people.pmc_id}`);
  const shown = visitor.getByRole("list", { name: "Photos, Aspen Row, Unit 2" }).getByRole("img");
  await expect(shown).toHaveCount(2);
  await expect(shown.nth(1)).toHaveAttribute("alt", "Aspen Row, Unit 2, 2 of 2");
  await expect.poll(() => shown.first().evaluate((img: HTMLImageElement) => img.complete && img.naturalWidth)).toBe(1);

  // Removed, a photo leaves the public page.
  await unit.getByRole("button", { name: "Photos, Aspen Row, Unit 2" }).click();
  await photos.getByRole("button", { name: "Remove Porch" }).click();
  await expect(photos.getByText("Removed Porch.")).toBeVisible();
  await visitor.reload();
  await expect(shown).toHaveCount(1);
  await visitor.close();
});

test("staff add a lead who asked about a unit, show it to them, record how it went and close the lead when they apply", async ({
  page,
}) => {
  const people = seed.features.leads!;
  await joinAsStaff(page, people.manager);
  await section(page, "Leads");
  await expect(page.getByText("No lead is open.")).toBeVisible();
  await page.getByRole("button", { name: "Add lead" }).click();
  const adding = page.getByRole("dialog", { name: "Add lead" });
  await adding.getByLabel("Name").fill("Prospect Lane");
  await adding.getByLabel("Email").fill("prospect.lane@example.com");
  await adding.getByRole("combobox", { name: "Property asked about", exact: true }).selectOption({ label: "Aspen Row" });
  await adding.getByRole("combobox", { name: "Unit asked about", exact: true }).selectOption({ label: "Unit 2" });
  await adding.getByLabel("Source (optional)").fill("Website");
  await adding.getByRole("button", { name: "Add lead" }).click();
  await expect(page.getByRole("heading", { level: 1, name: "Prospect Lane" })).toBeVisible();
  await expect(page.getByLabel("Lead details")).toContainText("Unit 2, Aspen Row");

  // A showing of the unit they asked about, tomorrow morning; it went ahead.
  await page.getByRole("button", { name: "Schedule showing" }).click();
  const showing = page.getByRole("dialog", { name: "Schedule showing" });
  await expect(showing.getByRole("combobox", { name: "Unit", exact: true })).toHaveValue(/.+/);
  await showing.getByLabel("Day").fill(daysFromToday(1));
  await showing.getByLabel("Time").fill("10:00");
  await showing.getByRole("button", { name: "Schedule" }).click();
  await expect(page.getByText(/^Showing on /)).toBeVisible();
  const row = page.getByRole("table", { name: "Showings" }).getByRole("row", { name: /^Showing / });
  await expect(row).toContainText("Unit 2, Aspen Row");
  await row.getByRole("button", { name: /^Actions for Showing / }).click();
  await page.getByRole("menuitem", { name: "Shown" }).click();
  await expect(row).toContainText("Shown");

  // They applied: the lead closes and takes no more showings.
  await page.getByRole("button", { name: "Close lead" }).click();
  const closing = page.getByRole("dialog", { name: "Close lead" });
  await closing.getByRole("combobox", { name: "Outcome", exact: true }).selectOption({ label: "They applied" });
  await closing.getByRole("button", { name: "Close lead" }).click();
  await expect(page.getByTestId("status")).toHaveText("Applied");
  await expect(page.getByRole("button", { name: "Schedule showing" })).toHaveCount(0);

  await section(page, "Leads");
  await expect(page.getByText("No lead is open.")).toBeVisible();
  await page.getByRole("tab", { name: "Closed", exact: true }).click();
  const closed = page.getByRole("row", { name: "Lead Prospect Lane" });
  await expect(closed.getByTestId("status")).toHaveText("Applied");
  await expect(closed.getByRole("cell").nth(5)).toHaveText("1");
});

test("staff set the screening criteria; an applicant reads them, is screened by them and declined against the recommendation, and reads the notice", async ({
  browser,
  page,
}) => {
  const people = seed.features["screening-criteria"]!;
  await joinAsStaff(page, people.manager);
  await settings(page, "Company setup");
  await expect(page.getByTestId("screening-criteria")).toHaveText(
    "Approved at 3 times the rent or more; on conditions at 2 times the rent; declined below",
  );
  await page.getByRole("button", { name: "Edit screening criteria" }).click();
  const editing = page.getByRole("dialog", { name: "Screening criteria" });
  await editing.getByLabel("Approve from (times the rent)").fill("2.5");
  await editing.getByLabel("Approve on conditions from (times the rent)").fill("1.5");
  await editing.getByLabel("In words, for applicants (optional)").fill("Household income of 2.5 times the rent.");
  await editing.getByRole("button", { name: "Save" }).click();
  await expect(page.getByTestId("screening-criteria")).toHaveText(
    "Approved at 2.5 times the rent or more; on conditions at 1.5 times the rent; declined below",
  );

  // A listed unit at $1,000.00: the applicant reads the criteria before applying, with 2.6 times the rent.
  await section(page, "Listings");
  const unit = page.getByRole("row", { name: "Aspen Row, Unit 2" });
  await unit.getByRole("button", { name: "List", exact: true }).click();
  const listing = page.getByRole("dialog", { name: "List on the public page" });
  await listing.getByLabel("Rent a month").fill("1000.00");
  await listing.getByRole("button", { name: "List it" }).click();
  await expect(page.getByText("Listed Aspen Row, Unit 2 at $1,000.00.")).toBeVisible();
  const visitor = await (await browser.newContext()).newPage();
  await visitor.goto(`/listings?pmc=${people.pmc_id}`);
  await visitor.getByRole("listitem", { name: "Aspen Row, Unit 2" }).getByRole("link", { name: "Apply" }).click();
  await expect(visitor.getByTestId("screening-criteria")).toContainText("Household income of 2.5 times the rent.");
  await visitor.getByLabel("Move-in day").fill(daysFromToday(20));
  await visitor.getByLabel("Monthly income of everyone applying, together").fill("2600");
  await visitor.getByLabel("Full name").fill("Applicant Criteria");
  await visitor.getByLabel("Email").fill("applicant.criteria@example.com");
  await visitor.getByRole("button", { name: "Send application" }).click();
  const statusUrl = new URL((await visitor.getByTestId("status-url").textContent()) ?? "");

  // Screened by the company's criteria, it is approved (2.6 times is under the partner's own 3).
  await section(page, "Applicants");
  await page.getByRole("row", { name: "Application from Applicant Criteria" }).click();
  await page.getByRole("button", { name: "Screen" }).click();
  await expect(page.getByRole("list", { name: "Screenings" })).toContainText("Recommends approval");

  // Staff decline it anyway: that says why, with the adverse action notice.
  await page.getByRole("button", { name: "Reject" }).click();
  const rejecting = page.getByRole("dialog", { name: "Reject" });
  await rejecting.getByLabel("Adverse action reasons, one a line").fill("A co-signer was asked for and not given");
  await rejecting.getByLabel("Why decide against the screening's recommendation").fill("The owner asked for a co-signer");
  await rejecting.getByRole("button", { name: "Reject" }).click();
  await expect(page.getByTestId("status")).toHaveText("Rejected");
  await expect(page.getByTestId("override")).toContainText("The owner asked for a co-signer");

  // The applicant reads the notice on their status page.
  await visitor.goto(statusUrl.pathname + statusUrl.hash);
  await expect(visitor.getByTestId("adverse-action-notice")).toContainText("Your application was declined.");
  await expect(visitor.getByTestId("adverse-action-notice")).toContainText("A co-signer was asked for and not given");
  await visitor.close();
});

test("staff move a tenant to another owner's unit with their deposit; the new lease's payment pays the old owner first", async ({
  page,
}) => {
  const moving = seed.features["lease-transfers"]!;
  await joinAsStaff(page, moving.manager);

  // Jamie Sample's lease at Birch Flats (Demo Owner A's) owes something more: a lost key.
  await openLease(page, "Birch Flats, Unit B");
  const charge = await leasePanel(page, "Enter charge");
  await charge.getByLabel("Add a").selectOption("fee");
  await charge.getByLabel("Amount", { exact: true }).fill("75.00");
  await charge.getByLabel("What for").fill("Lost key");
  await charge.getByRole("button", { name: "Add", exact: true }).click();
  await expect(page.getByText("Fee of $75.00 added.")).toBeVisible();

  // They move to Aspen Row's vacant Unit 2 (Demo Owner B's) today, having agreed in writing that
  // their deposit stays held.
  const move = await leasePanel(page, "Move to another unit");
  await move.getByLabel("Move to").selectOption({ label: "Aspen Row · Unit 2" });
  await move.getByLabel("Monthly rent there").fill("1240.00");
  await move.getByLabel(/agreed in writing that their .* deposit stays held, on/).fill(today());
  await move.getByRole("button", { name: "Move the tenants" }).click();
  await expect(page.getByText(/^Moved to Aspen Row · Unit 2\./)).toBeVisible();
  await expect(page.getByRole("heading", { level: 1, name: "Aspen Row · Unit 2 · Jamie Sample" })).toBeVisible();
  await expect(page.getByTestId("moved-from")).toContainText(`Moved here on ${us(today())} from Birch Flats · Unit B`);
  const carried = page.getByTestId("carried");
  await expect(carried).toContainText("still owed at Birch Flats · Unit B");
  const owedThere = /Includes (\$[\d,]+\.\d\d)/.exec((await carried.textContent()) ?? "")![1]!;
  await expect(page.getByTestId("balance-due")).toHaveText(owedThere);
  await expect(page.getByTestId("deposit-held")).not.toHaveText("$0.00");

  // A payment here pays what Birch Flats is owed first, into Demo Owner A's account.
  const pay = await leasePanel(page, "Receive payment");
  await pay.getByLabel("Payment received").fill(owedThere.replace(/[$,]/g, ""));
  await pay.getByLabel("Check or reference").fill("Check 901");
  await pay.getByRole("button", { name: "Record payment" }).click();
  await expect(page.getByText(`${owedThere} paid what is owed.`)).toBeVisible();
  await expect(page.getByTestId("balance-due")).toHaveText("$0.00");
  await expect(page.getByTestId("carried")).toHaveCount(0);

  // The old lease ended yesterday, is paid up and says where they went; Move-outs lists only the
  // new one, with the deposit.
  await page.getByRole("link", { name: "Birch Flats · Unit B" }).click();
  await expect(page.getByText(/The tenants moved to Aspen Row · Unit 2 on .*; their deposits went with them\./)).toBeVisible();
  await expect(page.getByTestId("balance-due")).toHaveText("$0.00");
  await section(page, "Move-outs");
  await expect(page.getByText("Aspen Row, Unit 2")).toBeVisible();
  await expect(page.getByText("Birch Flats, Unit B")).toHaveCount(0);
});

// What a calendar app reads from a link: iCalendar text, its long lines unfolded.
async function calendarAt(page: Page, url: string): Promise<{ status: number; type: string; text: string }> {
  const answer = await page.request.get(url);
  return {
    status: answer.status(),
    type: answer.headers()["content-type"] ?? "",
    text: (await answer.text()).replace(/\r\n[ \t]/g, ""),
  };
}

test("staff and a tenant each subscribe their calendar app to a link of their own; a new link takes the old one back", async ({
  browser,
  page,
}) => {
  const people = seed.features["calendar"]!;
  await joinAsStaff(page, people.manager);
  await section(page, "Settings");
  await page.getByRole("link", { name: "Your calendar" }).click();
  const card = part(page, "Calendar");
  await expect(card.getByTestId("calendar-state")).toHaveText("You have no calendar link.");
  await card.getByRole("button", { name: "Make a calendar link" }).click();
  await expect(card).toContainText("Made your calendar link.");
  const first = await card.getByLabel("Calendar link").inputValue();
  const feed = await calendarAt(page, first);
  expect([feed.status, feed.type]).toEqual([200, "text/calendar; charset=utf-8"]);
  expect(feed.text).toContain("BEGIN:VCALENDAR");
  expect(feed.text).toContain("SUMMARY:Take the company's fees out of Demo operating trust account");
  await expect(card.getByTestId("calendar-state")).toHaveText(`Your calendar link was made on ${us(today())}.`);

  // A new link takes the old one back; taking the new one back stops it too.
  await card.getByRole("button", { name: "Make a new link" }).click();
  await expect(card).toContainText("Made a new calendar link; the one before stopped working.");
  const second = await card.getByLabel("Calendar link").inputValue();
  expect(second).not.toBe(first);
  expect((await calendarAt(page, first)).status).toBe(404);
  expect((await calendarAt(page, second)).status).toBe(200);
  await card.getByRole("button", { name: "Take the link back" }).click();
  await page.getByRole("alertdialog").getByRole("button", { name: "Take it back" }).click();
  await expect(card.getByTestId("calendar-state")).toHaveText("You have no calendar link.");
  expect((await calendarAt(page, second)).status).toBe(404);

  // The tenant's calendar: their rent due days, and nothing of the company's.
  const tenant = await browser.newPage();
  await joinPortal(tenant, people.taylor);
  const theirs = part(tenant, "Calendar");
  await theirs.getByRole("button", { name: "Make a calendar link" }).click();
  await expect(theirs).toContainText("Made your calendar link.");
  const link = await theirs.getByLabel("Calendar link").inputValue();
  const mine = await calendarAt(tenant, link);
  expect(mine.status).toBe(200);
  expect(mine.text).toContain("SUMMARY:Rent due: Cedar House\\, House");
  expect(mine.text).not.toContain("Take the company");
  await theirs.getByRole("button", { name: "Take the link back" }).click();
  await tenant.getByRole("alertdialog").getByRole("button", { name: "Take it back" }).click();
  await expect(theirs.getByTestId("calendar-state")).toHaveText("You have no calendar link.");
  expect((await calendarAt(tenant, link)).status).toBe(404);
  await tenant.close();
});

test("a tenant changes their own phone and email in the portal, and staff see the new ones and that the tenant changed them", async ({
  browser,
  page,
}) => {
  const people = seed.features["own-contact"]!;
  await joinPortal(page, people.drew);
  const mine = page.getByRole("group", { name: "Contact details, Drew Sample" });
  await expect(mine).toContainText("No contact details yet.");
  await mine.getByRole("button", { name: "Edit contact details" }).click();
  await mine.getByLabel("Email").fill("drew.new@example.com");
  await mine.getByLabel("Phone", { exact: true }).fill("(555) 555-0142");
  await mine.getByLabel("City").fill("Sampletown");
  await mine.getByRole("button", { name: "Save contact details" }).click();
  // A mailing address is whole or not there: the tenant fills it in or clears it.
  await expect(mine.getByRole("alert")).toContainText("mailing address");
  await mine.getByLabel("City").fill("");
  await mine.getByRole("button", { name: "Save contact details" }).click();
  await expect(mine).toContainText("Contact details saved.");
  await expect(mine).toContainText("drew.new@example.com");
  await expect(mine).toContainText("(555) 555-0142");

  const staff = await browser.newPage();
  await joinAsStaff(staff, people.manager);
  await section(staff, "Tenants");
  await staff.getByRole("row", { name: "Drew Sample", exact: true }).getByRole("link", { name: "Drew Sample" }).click();
  const theirs = staff.getByRole("group", { name: "Contact details, Drew Sample" });
  await expect(theirs).toContainText("drew.new@example.com");
  await expect(theirs).toContainText(`Drew Sample changed these in the portal on ${us(today())}.`);
  await staff.close();
});

test("staff make a tenant a tenant at a second property and lease them a unit there; they sign in once, see both homes and ask for a repair at the new one", async ({
  browser,
  page,
}) => {
  const people = seed.features["two-homes"]!;
  await joinAsStaff(page, people.manager);
  await section(page, "Tenants");
  await page.getByRole("row", { name: /^Taylor Demo/ }).getByRole("link", { name: "Taylor Demo" }).click();
  const others = part(page, "Other properties");
  await expect(others).toContainText("Renting at another property too?");
  await others.getByRole("button", { name: "Rent at another property" }).click();
  const elsewhere = page.getByRole("dialog", { name: "Rent at another property" });
  await elsewhere.getByLabel("Property").selectOption({ label: "Aspen Row" });
  await elsewhere.getByRole("button", { name: "Make them a tenant there" }).click();
  await expect(page.getByText("Taylor Demo is a tenant at Aspen Row too: put them on the lease there.")).toBeVisible();
  // Their record at Aspen Row: they sign in as their first, at Cedar House.
  await expect(page.getByRole("link", { name: "Aspen Row", exact: true })).toBeVisible();
  await expect(part(page, "Portal")).toContainText("signs in once, as their record at Cedar House");
  await expect(part(page, "Other properties").getByRole("link", { name: "Tenant at Cedar House" })).toBeVisible();

  // Aspen Row's vacant Unit 2, leased to them from today.
  await section(page, "Properties");
  await page.getByRole("link", { name: "Aspen Row", exact: true }).click();
  await page.getByRole("row", { name: /^Unit 2/ }).getByRole("link", { name: "Unit 2", exact: true }).click();
  await page.getByRole("button", { name: "Add lease in place" }).click();
  const lease = page.getByRole("dialog", { name: "Add lease" });
  await lease.getByLabel("Taylor Demo").check();
  await lease.getByLabel("Starts").fill(today());
  await lease.getByLabel("Rent a month").fill("1240.00");
  await lease.getByRole("button", { name: "Add lease" }).click();
  await expect(page.getByRole("row", { name: /Taylor Demo/ })).toContainText(`${us(today())} – Month to month`);

  // Drew Sample at Aspen Row isn't Taylor: and anyone who signs in is linked to nobody.
  await section(page, "Tenants");
  await page.getByRole("row", { name: /^Drew Sample/ }).getByRole("link", { name: "Drew Sample" }).click();
  await part(page, "Other properties").getByRole("button", { name: "Same person as…" }).click();
  const same = page.getByRole("dialog", { name: "Same person as…" });
  await same.getByLabel("Name, email or phone").fill("Taylor");
  await same.getByRole("button", { name: "Find" }).click();
  await same.getByRole("button", { name: /^Same person: Taylor Demo, .*Cedar House/ }).click();
  await expect(same.getByRole("alert")).toContainText("Drew Sample signs in to the portal: link Taylor Demo to them instead");

  // Taylor signs in once and has both homes; a repair asked for at Aspen Row is theirs there.
  const tenant = await browser.newPage();
  await joinPortal(tenant, people.taylor);
  const picker = tenant.getByRole("combobox", { name: /^Lease/ });
  await picker.selectOption({ label: `Aspen Row, Unit 2, from ${us(today())}` });
  await expect(part(tenant, "Your lease")).toContainText("Aspen Row, Unit 2");
  await tenant.getByLabel("What needs fixing?").fill("The porch light flickers");
  await tenant.getByRole("button", { name: "Send request" }).click();
  await expect(part(tenant, "Maintenance requests").getByRole("listitem").filter({ hasText: "The porch light flickers" }))
    .toContainText("Received");
  // Newest first: the lease at Aspen Row, then the one at Cedar House.
  await picker.selectOption({ index: 1 });
  await expect(part(tenant, "Your lease")).toContainText("Cedar House, House");
  await tenant.close();
});

test("staff approve last December in each trust bank account, close the year, and a charge dated in it is refused in words", async ({
  page,
}) => {
  const people = seed.features["year-close"]!;
  const year = new Date().getUTCFullYear() - 1;
  const december = `12/31/${year}`;
  await joinAsStaff(page, people.manager);
  await section(page, "Banking");
  const card = part(page, "Year close");
  const ready = card.getByRole("table", { name: `Ready to close ${year}` });
  await expect(ready.getByRole("row", { name: "Demo operating trust account" })).toContainText("Approve December");
  await expect(card.getByRole("button", { name: `Close ${year}` })).toBeDisabled();

  // The bookkeeper prepared December in each account; the manager approves both.
  for (const account of ["Demo operating trust account", "Demo security deposit account"]) {
    await ready.getByRole("link", { name: account }).click();
    await page.getByRole("button", { name: `Approve through ${december}` }).click();
    await expect(page.getByText(`Approved through ${december}; the account is closed through it.`)).toBeVisible();
    await section(page, "Banking");
  }
  for (const account of ["Demo operating trust account", "Demo security deposit account"]) {
    await expect(ready.getByRole("row", { name: account })).toContainText(`${december}Ready`);
  }
  await card.getByRole("button", { name: `Close ${year}` }).click();
  const asked = page.getByRole("alertdialog");
  await expect(asked).toContainText("can't be reopened");
  await asked.getByRole("button", { name: `Close ${year}` }).click();
  await expect(card.getByTestId("closed-until")).toHaveText(
    `Closed through ${december}: nothing dated then or before is charged or billed.`,
  );
  await expect(card.getByRole("list", { name: "Years closed" })).toContainText(`${year}, closed on ${us(today())} by Demo Manager`);

  // A fee dated in the closed year is refused, saying why; dated today it is added.
  const lease = await openLease(page, "Birch Flats, Unit A");
  const charge = await leasePanel(page, "Enter charge");
  await charge.getByLabel("Add a").selectOption("fee");
  await charge.getByLabel("Amount", { exact: true }).fill("25.00");
  await charge.getByLabel("Due on").fill(`${year}-12-20`);
  await charge.getByLabel("What for").fill("Key replacement");
  await charge.getByRole("button", { name: "Add", exact: true }).click();
  await expect(page.getByText(
    `${year} is closed: a charge dated 12/20/${year} can't be added; date it 01/01/${year + 1} or later`,
  ).first()).toBeVisible();
  await charge.getByLabel("Due on").fill(today());
  await charge.getByRole("button", { name: "Add", exact: true }).click();
  await expect(lease).toContainText("Fee of $25.00 added.");
});

test("staff bring a property's units, tenants, lease and unpaid charges in from files, and open two new trust bank accounts with the owner's balance and the tenant's deposit", async ({
  page,
}) => {
  const people = seed.features["switching-balances"]!;
  await joinAsStaff(page, people.manager);
  // Two trust bank accounts with nothing in them yet, one of each kind.
  for (const [holds, name] of [
    ["Operating: rent and owners' money", "Harbor operating trust account"],
    ["Security deposits", "Harbor deposit account"],
  ] as const) {
    await section(page, "Banking");
    await page.getByRole("button", { name: "Add bank account" }).click();
    const adding = page.getByRole("dialog", { name: "Add bank account" });
    await adding.getByLabel("Holds").selectOption({ label: holds });
    await adding.getByLabel("Bank account name").fill(name);
    await adding.getByRole("button", { name: "Add bank account" }).click();
    await expect(page.getByRole("heading", { level: 1, name })).toBeVisible();
  }

  // The roster, a file at a time; sent again, a file adds nothing twice.
  await settings(page, "Import from another PMS");
  const roster = part(page, "Bring the roster in");
  const bring = async (label: string, csv: string, said: string) => {
    await roster.getByRole("combobox", { name: "File of" }).selectOption({ label });
    await roster.getByLabel("CSV file").setInputFiles({ name: "roster.csv", mimeType: "text/csv", buffer: Buffer.from(csv) });
    await roster.getByRole("button", { name: "Import" }).click();
    await expect(roster).toContainText(said);
  };
  const started = firstOfMonthsAgo(3);
  const owed = firstOfMonthsAgo(1);
  await bring("Properties and their owners",
    "Property,Owner,Bank account\nHarbor View,Demo Owner B,Harbor operating trust account\n", "1 added.");
  const units = "Property,Unit\nHarbor View,Unit 1\nHarbor View,Unit 2\nHarbor View,Unit 3\n";
  await bring("Units", units, "3 added.");
  await bring("Units", units, "0 added; 3 already here.");
  await bring("Tenants", "Property,Tenant\nHarbor View,Reese Sample\nHarbor View,Rowan Sample\n", "2 added.");
  await bring("Leases in place",
    `Property,Unit,Tenants,Starts on,Monthly rent\nHarbor View,Unit 1,Reese Sample,${started},975.00\n`,
    "1 added; 1 already here.");
  await bring("Unpaid charges",
    "Property,Unit,Due on,Amount,Kind,Memo\n"
      + `Harbor View,Unit 1,${owed},975.00,rent,\n`
      + `Harbor View,Unit 1,${owed},40.00,fee,Late fee from the old system\n`,
    "2 added.");

  // Each new account opens with what it held at the start of this month: the owner's money in the
  // operating one, the tenant's deposit in the deposit one.
  const open = async (account: string, cash: string, csv: string) => {
    const card = part(page, `Cutover: ${account}`);
    await card.getByLabel("Cutover date").fill(firstOfThisMonth());
    await card.getByLabel("Old system's book cash").fill(cash);
    await card.getByLabel("Opening balances (CSV)").setInputFiles({ name: "balances.csv", mimeType: "text/csv", buffer: Buffer.from(csv) });
    await card.getByRole("button", { name: "Open with balances" }).click();
    await expect(card).toContainText("Opened with 1 balance.");
  };
  await open("Harbor operating trust account", "1500.00", "Kind,Owner,Property,Amount\nowner,Demo Owner B,Harbor View,1500.00\n");
  await open("Harbor deposit account", "975.00", "Kind,Property,Tenant,Amount\ndeposit,Harbor View,Reese Sample,975.00\n");

  // The property has its three units; the lease holds the deposit and owes what was unpaid; the
  // owner holds the balance.
  await openProperty(page, "Harbor View");
  for (const unit of ["Unit 1", "Unit 2", "Unit 3"]) await expect(page.getByRole("row", { name: unit })).toBeVisible();
  const lease = await openLease(page, "Harbor View, Unit 1");
  await expect(lease.getByTestId("deposit-held")).toHaveText("$975.00");
  await expect(lease.getByTestId("balance-due")).toHaveText("$1,015.00");
  const charges = lease.getByRole("list", { name: "Open charges" });
  await expect(charges).toContainText(`${us(owed)} Rent: $975.00 open`);
  await expect(charges).toContainText(`${us(owed)} Late fee from the old system: $40.00 open`);
  await expect((await ownerProperty(page, "Demo Owner B", "Harbor View")).getByRole("cell").nth(2)).toHaveText("$1,500.00");
});

test("staff bring the old system's tenant, owner and vendor history in once, and read it under Before the switch on each page", async ({
  page,
}) => {
  const people = seed.features["history"]!;
  await joinAsStaff(page, people.manager);
  await settings(page, "Import from another PMS");
  const roster = part(page, "Bring the roster in");
  const bring = async (label: string, name: string, csv: string, said: string) => {
    await roster.getByRole("combobox", { name: "File of" }).selectOption({ label });
    await roster.getByLabel("CSV file").setInputFiles({ name, mimeType: "text/csv", buffer: Buffer.from(csv) });
    await roster.getByRole("button", { name: "Import" }).click();
    await expect(roster).toContainText(said);
  };
  const tenants =
    "Property,Tenant,Unit,Date,Type,Description,Amount,Reference\n" +
    "Cedar House,Taylor Demo,House,2024-05-01,Charge,May rent,2100.00,\n" +
    "Cedar House,Taylor Demo,House,2024-05-03,Payment,Check 1201,-2100.00,1201\n" +
    "Elm Court,Robin Former,Unit 1,2023-12-01,Charge,December rent,1300.00,\n";
  await bring("Tenants' history before the switch", "tenants.csv", tenants, "3 added.");
  // Sent again: nothing twice.
  await bring("Tenants' history before the switch", "tenants.csv", tenants, "0 added; 3 already here.");
  await bring("Owners' history before the switch", "owners.csv",
    "Owner,Property,Date,Type,Description,Amount\nDemo Owner A,Cedar House,2024-05-31,Owner draw,May draw,-1850.00\n",
    "1 added.");
  await bring("Vendors' history before the switch", "vendors.csv",
    "Vendor,Date,Type,Description,Amount,Reference\nDemo Plumbing Co,2024-04-12,Payment,Water heater,640.00,CHK-88\n",
    "1 added.");

  // Taylor's lines, newest first, on their Leases tab.
  await section(page, "Tenants");
  await page.getByRole("row", { name: /^Taylor Demo/ }).getByRole("link", { name: "Taylor Demo" }).click();
  await page.getByRole("tab", { name: "Leases" }).click();
  const before = page.getByRole("table", { name: "Before the switch" });
  await expect(before.getByRole("row")).toHaveText([
    "DateUnitTypeDescriptionReferenceAmount",
    "05/03/2024HousePaymentCheck 12011201($2,100.00)",
    "05/01/2024HouseChargeMay rent$2,100.00",
  ]);
  // The owner's under Statements, the vendor's under Bills.
  await section(page, "Owners");
  await page.getByRole("row", { name: /^Demo Owner A/ }).getByRole("link", { name: "Demo Owner A" }).click();
  await page.getByRole("tab", { name: "Statements" }).click();
  await expect(page.getByRole("table", { name: "Before the switch" })).toContainText("Cedar HouseOwner drawMay draw");
  await section(page, "Vendors");
  await page.getByRole("link", { name: "Demo Plumbing Co", exact: true }).first().click();
  await page.getByRole("tab", { name: "Bills" }).click();
  await expect(page.getByRole("table", { name: "Before the switch" })).toContainText("04/12/2024PaymentWater heaterCHK-88$640.00");
});

test("staff add charge types and give a lease's fee and monthly charge one; the owner's income statement counts the type on its own line", async ({
  browser,
  page,
}) => {
  const people = seed.features["charge-types"]!;
  await joinAsStaff(page, people.manager);
  await settings(page, "Chart of accounts");
  for (const name of ["Key replacement", "Pet fees"]) {
    await page.getByRole("button", { name: "Add charge type" }).click();
    const adding = page.getByRole("dialog", { name: "Add charge type" });
    await adding.getByLabel("Charge type").fill(name);
    await adding.getByRole("button", { name: "Add charge type" }).click();
    await expect(page.getByRole("list", { name: "Charge types" })).toContainText(name);
  }

  // A lost key charged as a Key replacement fee and paid; a dog's monthly fee as Pet fees.
  const lease = await openLease(page, "Birch Flats, Unit A");
  const charge = await leasePanel(page, "Enter charge");
  await charge.getByLabel("Amount").fill("75.00");
  await charge.getByLabel("What for").fill("Lost key");
  await charge.getByRole("combobox", { name: "Type" }).selectOption({ label: "Key replacement" });
  await charge.getByRole("button", { name: "Add", exact: true }).click();
  await expect(page.getByText("Fee of $75.00 added.")).toBeVisible();
  const pay = await leasePanel(page, "Receive payment");
  await pay.getByLabel("Payment received").fill("75.00");
  await expect(page.getByTestId("toaster")).toHaveCount(0, { timeout: 10_000 });
  await pay.getByRole("button", { name: "Record payment" }).click();
  await expect(page.getByText("$75.00 paid what is owed.")).toBeVisible();
  const nextMonth = new Date(Date.UTC(Number(today().slice(0, 4)), Number(today().slice(5, 7)), 1))
    .toISOString()
    .slice(0, 10);
  const monthly = await leasePanel(page, "Add recurring charge");
  await monthly.getByLabel("Monthly amount").fill("25.00");
  await monthly.getByLabel("First due on").fill(nextMonth);
  await monthly.getByLabel("Monthly, for").fill("Dog");
  await monthly.getByRole("combobox", { name: "Type" }).selectOption({ label: "Pet fees" });
  await monthly.getByRole("button", { name: "Add every month" }).click();
  await expect(lease).toContainText(`Dog (Pet fees): $25.00 a month from ${us(nextMonth)}`);

  // The owner's income statement this year: the key on a line of its own.
  const owner = await (await browser.newContext()).newPage();
  await joinPortal(owner, people.owner_a);
  const card = owner.locator("section", { has: owner.getByRole("heading", { level: 2, name: "Income statement" }) });
  await card.getByLabel("Year").fill(today().slice(0, 4));
  await card.getByRole("button", { name: "Show" }).click();
  await expect(card.getByRole("table", { name: "Birch Flats" }).getByRole("row", { name: "Key replacement $75.00" })).toBeVisible();
  await owner.close();
});

test("staff name the operating trust account's cash for QuickBooks and download two months of journal entries for QuickBooks Desktop and Online", async ({
  page,
}) => {
  const people = seed.features.quickbooks!;
  await joinAsStaff(page, people.manager);
  await settings(page, "QuickBooks");
  const names = page.getByRole("table", { name: "Account names" });
  await expect(names).toContainText("Owner funds:Demo Owner A - Birch Flats");
  await page.getByRole("button", { name: "Rename Trust cash:Demo operating trust account" }).click();
  const panel = page.getByRole("dialog", { name: "Name in QuickBooks" });
  await panel.getByLabel("Name in QuickBooks").fill("Trust checking");
  await panel.getByRole("button", { name: "Save" }).click();
  await expect(names.getByRole("row", { name: "Trust checking" })).toBeVisible();

  await page.getByLabel("From").fill(firstOfMonthsAgo(2));
  await page.getByLabel("To").fill(today());
  const desktop = await page.request.get(
    (await page.getByRole("link", { name: "For QuickBooks Desktop (IIF)" }).getAttribute("href")) ?? "",
  );
  expect(desktop.status()).toBe(200);
  const iif = await desktop.text();
  expect(iif.startsWith("!TRNS\tTRNSID\tTRNSTYPE\tDATE\tACCNT\tAMOUNT\tDOCNUM\tMEMO")).toBe(true);
  expect(iif).toContain("GENERAL JOURNAL");
  expect(iif).toContain("\tTrust checking\t");
  const online = await page.request.get(
    (await page.getByRole("link", { name: "For QuickBooks Online (CSV)" }).getAttribute("href")) ?? "",
  );
  expect(online.headers()["content-type"]).toContain("text/csv");
  const csv = await online.text();
  expect(csv.split("\r\n")[0]).toBe("Journal No.,Journal Date,Account Name,Debits,Credits,Description,Name");
  expect(csv).toContain("Trust checking");
});


test("staff bring in a lockbox's payment file once: lines naming one lease are recorded to it, and staff record or set aside the ones that wait", async ({
  page,
}) => {
  const people = seed.features["payment-files"]!;
  await joinAsStaff(page, people.manager);
  await section(page, "Payment files");
  await expect(page.getByText("No payment file has come in yet.")).toBeVisible();

  const day = today();
  const lockbox =
    "Date,Amount,Property,Unit,Payer,Reference,Method\n" +
    `${day},1395.00,Birch Flats,Unit A,,2201,check\n` +
    `${day},100.00,,Unit 1,,,\n` + // Elm Court and Aspen Row both have a Unit 1
    `${day},50.00,,,Jamie Sample,,ACH\n`;
  const bringIn = async (name: string, csv: string) => {
    await page.getByRole("button", { name: "Bring in a file" }).click();
    const panel = page.getByRole("dialog", { name: "Bring in a payment file" });
    await panel.getByLabel("Payment file (CSV)").setInputFiles({ name, mimeType: "text/csv", buffer: Buffer.from(csv) });
    return panel;
  };

  // What each line will do, before anything is written.
  const panel = await bringIn("lockbox.csv", lockbox);
  await expect(panel.getByTestId("preview-summary")).toHaveText(
    "2 payments ($1,445.00) will be recorded; 1 will wait for you.",
  );
  await expect(panel.getByRole("table", { name: "What the file will do" }).getByRole("row")).toHaveText([
    "LineReceivedAmountNamesWhat happens",
    `2${us(day)}$1,395.00Birch Flats Unit ATo record Morgan Demo, Birch Flats Unit A`,
    `3${us(day)}$100.00Unit 1Waiting 2 leases in effect on ${us(day)} at unit Unit 1; name the property too`,
    `4${us(day)}$50.00Jamie SampleTo record Jamie Sample, Birch Flats Unit B`,
  ]);
  await panel.getByRole("button", { name: "Bring it in" }).click();
  await expect(page.getByRole("heading", { level: 1, name: "lockbox.csv" })).toBeVisible();
  await expect(page.getByTestId("file-summary")).toContainText("lockbox.csv: 2 payments recorded ($1,445.00); 1 wait for you.");
  await expect(page.getByRole("row", { name: "Line 2" })).toContainText("RecordedMorgan Demo, Birch Flats Unit A");

  // The line that waits goes to the lease staff find.
  await page.getByRole("row", { name: "Line 3" }).getByRole("button", { name: "Actions for line 3" }).click();
  await page.getByRole("menuitem", { name: "Record to a lease" }).click();
  const record = page.getByRole("dialog", { name: "Record line 3" });
  await record.getByLabel("Lease").fill("Riley");
  await record.getByRole("button", { name: "Elm Court · Unit 1 · Riley Demo" }).click();
  await record.getByRole("button", { name: "Record $100.00 to this lease" }).click();
  await expect(page.getByText(`Line 3: $100.00 received ${us(day)} recorded to Riley Demo, Elm Court Unit 1.`)).toBeVisible();
  await expect(page.getByTestId("file-summary")).toContainText("3 payments recorded ($1,545.00); nothing waits.");

  // The same file again comes in once; a line naming nobody is set aside, with why.
  await section(page, "Payment files");
  const again = await bringIn("lockbox-again.csv", lockbox);
  await expect(again.getByText(`This file came in on ${us(day)}; it comes in once.`)).toBeVisible();
  await expect(again.getByRole("button", { name: "Bring it in" })).toHaveCount(0);
  await again.getByRole("button", { name: "Close" }).click();
  const stray = await bringIn("stray.csv", `Date,Amount,Payer\n${day},12.34,Nobody Sample\n`);
  await stray.getByRole("button", { name: "Bring it in" }).click();
  await expect(page.getByRole("heading", { level: 1, name: "stray.csv" })).toBeVisible();
  await page.getByRole("row", { name: "Line 2" }).getByRole("button", { name: "Actions for line 2" }).click();
  await page.getByRole("menuitem", { name: "Set aside" }).click();
  const aside = page.getByRole("dialog", { name: "Set line 2 aside" });
  await aside.getByLabel("Why").fill("A refund from the water utility, not rent");
  await aside.getByRole("button", { name: "Set it aside" }).click();
  await expect(page.getByRole("row", { name: "Line 2" })).toContainText("Set asideA refund from the water utility, not rent");

  // Both files are listed, newest first; the payment is on Morgan Demo's lease.
  await section(page, "Payment files");
  await expect(page.getByRole("table", { name: "Payment files" }).getByRole("row")).toHaveCount(3);
  await page.getByRole("row", { name: "lockbox.csv" }).click();
  await page.getByRole("row", { name: "Line 2" }).getByRole("link", { name: "Morgan Demo, Birch Flats Unit A" }).click();
  await page.getByRole("tab", { name: "Ledger" }).click();
  await expect(page.getByRole("main")).toContainText("$1,395.00");
});

test("staff keep the company's own notice templates, make one into a notice for a lease, read it filled in and download its PDF", async ({
  page,
}) => {
  const people = seed.features["notice-templates"]!;
  await joinAsStaff(page, people.manager);
  await settings(page, "Notice templates");
  await expect(page.getByText("No notice template yet.")).toBeVisible();

  // A blank the PMS doesn't fill is refused in words; the ones it does are listed beside the text.
  await page.getByRole("button", { name: "Add template" }).click();
  const add = page.getByRole("dialog", { name: "Add a notice template" });
  await expect(add.getByRole("region", { name: "Blanks the PMS fills" })).toContainText("{{balance_due}}");
  await add.getByLabel("Name").fill("Late rent");
  await add.getByLabel("Text").fill("Dear {{tenant_names}}, please pay {{amount}}.");
  await add.getByRole("button", { name: "Save template" }).click();
  await expect(add.getByRole("alert")).toHaveText(
    "{{amount}} is not a blank the PMS fills; the list beside the text shows the ones it does",
  );
  await add.getByLabel("Text").fill(
    "Dear {{tenant_names}},\n\nAs of {{notice_date}} the lease of {{property}} {{unit}} owes {{balance_due}}.\n\n{{company_name}}",
  );
  await add.getByRole("button", { name: "Save template" }).click();
  await expect(page.getByText("Late rent is saved.")).toBeVisible();

  // Renamed later: a new version.
  await page.getByRole("row", { name: "Late rent" }).click();
  const edit = page.getByRole("dialog", { name: "Edit Late rent" });
  await edit.getByLabel("Name").fill("Notice of late rent");
  await edit.getByRole("button", { name: "Save template" }).click();
  await expect(page.getByText("Notice of late rent is saved.")).toBeVisible();

  // On Morgan Demo's lease, its Files tab makes it into a notice, read filled in first.
  await section(page, "Rent roll");
  await page.getByRole("row", { name: "Birch Flats, Unit A" }).click();
  await page.getByRole("button", { name: "Actions for this lease" }).click();
  await page.getByRole("menuitem", { name: "Write a notice" }).click();
  await page.getByRole("button", { name: "Write a notice" }).click();
  const filled = page.getByRole("article", { name: "The notice as it will read" });
  await expect(filled).toContainText("Notice of late rent");
  await expect(filled).toContainText(`Dear Morgan Demo,`);
  await expect(filled).toContainText(`As of ${us(today())} the lease of Birch Flats Unit A owes $`);
  await page.getByRole("button", { name: "Make the notice" }).click();
  await expect(page.getByText(`Notice of late rent, dated ${us(today())}, is made.`)).toBeVisible();
  const pdf = page.getByRole("list", { name: "Notices made" }).getByRole("link", { name: "Notice of late rent (PDF)" });
  const answer = await page.request.get((await pdf.getAttribute("href")) ?? "");
  expect(answer.status()).toBe(200);
  expect((await answer.body()).subarray(0, 5).toString()).toBe("%PDF-");

  // Removing the template keeps the notice made from it.
  await settings(page, "Notice templates");
  await page.getByRole("row", { name: "Notice of late rent" }).getByRole("button", { name: "Actions for Notice of late rent" }).click();
  await page.getByRole("menuitem", { name: "Remove" }).click();
  await page.getByRole("button", { name: "Remove" }).click();
  await expect(page.getByText("No notice template yet.")).toBeVisible();
  await section(page, "Rent roll");
  await page.getByRole("row", { name: "Birch Flats, Unit A" }).click();
  await page.getByRole("tab", { name: "Files" }).click();
  await expect(page.getByRole("list", { name: "Notices made" })).toContainText("Notice of late rent (PDF)");
  // The saved toasts sit over the page's corner until they go.
  await expect(page.getByTestId("toaster")).toHaveCount(0, { timeout: 10_000 });
});

test("a tenant turns two-step sign-in on and signs in with a code; staff require it, and owners without it set it up at sign-in or from their invite", async ({
  browser,
  page,
}) => {
  const people = seed.features["two-step"]!;
  // An owner with a login from before the company required it, signed out.
  const owner = await (await browser.newContext()).newPage();
  await joinPortal(owner, people.owner_a);
  await owner.getByRole("button", { name: "Sign out" }).click();
  await expect(owner.getByRole("heading", { name: "Sign in" })).toBeVisible();

  // The tenant turns it on in the portal: the app's key, then its first code.
  await joinPortal(page, people.taylor);
  const card = page.getByRole("group", { name: "Two-step sign-in" });
  await expect(card.getByTestId("two-step-state")).toHaveText("Off: you sign in with your password alone.");
  await card.getByRole("button", { name: "Turn on two-step sign-in" }).click();
  const secret = (await card.locator("code").textContent()) ?? "";
  await card.getByLabel("Code from your authenticator app").fill(totp(secret));
  await card.getByRole("button", { name: "Turn on" }).click();
  await expect(card.getByTestId("two-step-state")).toHaveText(
    "On: each sign-in asks for your password and a code from your authenticator app.",
  );

  // Signed out, they sign in with the password, then the authenticator's next code.
  await page.getByRole("button", { name: "Sign out" }).click();
  await expect(page.getByRole("heading", { name: "Sign in" })).toBeVisible();
  await page.getByLabel("Email").fill(people.emails.taylor!);
  await page.getByLabel("Password").fill(PASSWORD);
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page.getByRole("alert")).toHaveText("enter the code from your authenticator app");
  await page.getByLabel("Code from your authenticator app").fill(totp(secret, Date.now() + 30_000));
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page.getByRole("navigation", { name: "Sections" })).toBeVisible();

  // Staff see whose sign-in asks for a code, and require it of every owner and tenant.
  const staff = await (await browser.newContext()).newPage();
  await joinAsStaff(staff, people.manager);
  await settings(staff, "Logins");
  await expect(staff.getByRole("row", { name: "Taylor Demo", exact: true }).getByRole("cell").nth(4)).toHaveText("On");
  await expect(staff.getByTestId("two-step-required")).toHaveText(
    "Each owner and tenant chooses whether to turn it on in their portal. Staff always sign in with a code.",
  );
  await staff.getByRole("button", { name: "Require two-step sign-in" }).click();
  await staff.getByRole("alertdialog").getByRole("button", { name: "Require it" }).click();
  await expect(staff.getByTestId("two-step-required")).toContainText("Required: every owner and tenant");
  await expect(staff.getByText(/^Changed by Demo Manager on \d\d\/\d\d\/\d{4}\.$/)).toBeVisible();

  // The tenant, still signed in, can't turn it off now.
  await card.getByLabel("Code from your authenticator app").fill(totp(secret, Date.now() + 60_000));
  await card.getByRole("button", { name: "Turn off" }).click();
  await expect(card.getByRole("alert")).toHaveText("your property manager requires two-step sign-in, so it stays on");

  // The owner without it signs in: after the password they set it up, and its code lets them in.
  await owner.getByLabel("Email").fill(people.emails.owner_a!);
  await owner.getByLabel("Password").fill(PASSWORD);
  await owner.getByRole("button", { name: "Sign in" }).click();
  await expect(owner.getByText(/^Your property manager requires two-step sign-in\. Scan this/)).toBeVisible();
  await expectAccessible(owner, "two-step sign-in set up at sign-in");
  await owner.getByLabel("Code from your authenticator app").fill(totp((await owner.locator("code").textContent()) ?? ""));
  await owner.getByRole("button", { name: "Sign in" }).click();
  await expect(owner.getByRole("group", { name: "Two-step sign-in" }).getByTestId("two-step-state")).toHaveText(
    "On: each sign-in asks for your password and a code from your authenticator app. Your property manager" +
      " requires it, so it stays on.",
  );

  // An owner invited now sets it up from the invite, after their password.
  const invited = await (await browser.newContext()).newPage();
  await invited.goto(`/invite#${people.owner_b}`);
  await invited.getByLabel("Choose a password (12 characters or more)").fill(PASSWORD);
  await invited.getByLabel("The same password again").fill(PASSWORD);
  await invited.getByRole("button", { name: "Set password" }).click();
  await expect(invited.getByText(/^Your property manager requires two-step sign-in: you sign in with a code/)).toBeVisible();
  await invited.getByLabel("Code").fill(totp((await invited.locator("code").textContent()) ?? ""));
  await invited.getByRole("button", { name: "Finish" }).click();
  await expect(invited.getByRole("navigation", { name: "Sections" })).toBeVisible();
  for (const other of [owner, staff, invited]) await other.context().close();
});
