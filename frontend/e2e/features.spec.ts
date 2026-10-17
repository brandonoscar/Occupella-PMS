// Each feature of the portal used end to end in a real browser, on a demo PMC's books of its own
// (demo.books in src/pms/demo.py, seeded by e2e/seed.py): two months of rent, bills, fees, draws
// and month-end, last month prepared and waiting for its approval. ci/features.toml names the test that proves
// each feature, and tools/check_features.py keeps the two in step.
import { readFileSync } from "node:fs";
import { expect, type Locator, type Page, test } from "@playwright/test";
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
  const search = page.getByLabel("Search properties, units, tenants and owners");
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
  const search = page.getByLabel("Search properties, units, tenants and owners");
  await search.fill("owner b");
  await page.getByRole("list", { name: "Search results" }).getByRole("link", { name: /Demo Owner B/ }).click();
  await expect(page.getByRole("heading", { level: 1, name: "Demo Owner B" })).toBeVisible();
});

test("staff add a fee, a credit and a monthly charge to a lease, take a deposit and prepaid rent, and a bounce undoes a payment and charges the NSF fee", async ({
  page,
}) => {
  const people = seed.features.recurring!;
  await joinAsStaff(page, people.manager);

  // The property's NSF fee, in Setup.
  await settings(page, "Company setup");
  const aspen = page.getByRole("listitem", { name: "Aspen Row" });
  await expect(aspen).toContainText("No NSF fee");
  await aspen.getByText("Add to Aspen Row").click();
  await aspen.getByLabel("NSF fee from").fill(firstOfThisMonth());
  await aspen.getByLabel("NSF fee", { exact: true }).fill("35.00");
  await aspen.getByRole("button", { name: "Set NSF fee" }).click();
  await expect(aspen).toContainText("NSF fee $35.00");

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

  // A payment when nothing is owed is all prepaid rent; it is listed, and it can bounce.
  pay = await leasePanel(page, "Receive payment");
  await pay.getByLabel("Payment received").fill("50.00");
  await pay.getByLabel("Check or reference").fill("Check 503");
  await pay.getByRole("button", { name: "Record payment" }).click();
  await expect(lease).toContainText("$50.00 held as prepaid rent.");
  const prepaidOnly = received.getByRole("row", { name: /Check 503/ });
  await expect(prepaidOnly).toContainText("$50.00");
  await prepaidOnly.getByRole("button", { name: /^Actions for / }).click();
  await prepaidOnly.getByRole("menuitem", { name: "It bounced" }).click();
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
  await lease.getByRole("menuitem", { name: "Stop" }).click();
  await lease.getByLabel("Stops on, Parking").fill(nextMonth);
  await lease.getByRole("button", { name: "Stop it" }).click();
  await expect(lease).toContainText(`Parking stops ${us(nextMonth)}.`);
  await expect(lease).toContainText(`Parking: $40.00 a month from ${us(nextMonth)}, stops ${us(nextMonth)}`);

  // A deposit held for the tenant.
  const deposit = await leasePanel(page, "Record deposit");
  await deposit.getByLabel("Deposit received", { exact: true }).fill("100.00");
  await deposit.getByRole("button", { name: "Record deposit" }).click();
  await expect(lease).toContainText("Deposit of $100.00 held.");
  await expect(lease.getByTestId("deposit-held")).toHaveText("$1,280.00");

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
  await payment.getByRole("menuitem", { name: "It bounced" }).click();
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
  await expect(admin.getByText("Added Demo Leasing Agent; invite them from Settings, Logins.")).toBeVisible();
  await expect(admin.getByRole("checkbox", { name: "Demo Leasing Agent: Approve" })).not.toBeChecked();

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
  const kept = cents + 10_000;
  const back = 118_000 - kept;
  await expect(lease).toContainText(`Keeps ${dollars(kept)} for what the lease owes; ${dollars(back)} goes back.`);
  await lease.getByRole("button", { name: "Return the deposit" }).click();
  // Money leaving trust waits for staff to confirm the amounts.
  await expect(lease.getByRole("alertdialog")).toContainText(
    `keep ${dollars(kept)} for what the lease owes and pay ${dollars(back)} back to the tenants?`,
  );
  await lease.getByRole("button", { name: "Confirm" }).click();
  await expect(lease).toContainText(`Returned ${dollars(back)} to the tenants; the rest paid what the lease owed.`);
  await expect(lease.getByText("Deposit returned")).toBeVisible();
  await downloads(page, "Statement for the tenants (PDF)", PDF);

  // The tenant downloads the same statement from the portal.
  const tenant = await (await browser.newContext()).newPage();
  await joinPortal(tenant, people.drew);
  await downloads(tenant, "Deposit statement (PDF)", PDF);
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

  // A trust bank account added from Banking opens its own page, and the setup lists it.
  await section(page, "Banking");
  await page.getByRole("button", { name: "Add bank account" }).click();
  const adding = page.getByRole("dialog", { name: "Add bank account" });
  await adding.getByLabel("Holds").selectOption({ label: "Operating: rent and owners' money" });
  await adding.getByLabel("Bank account name").fill("Second operating account");
  await adding.getByRole("button", { name: "Add bank account" }).click();
  await expect(page.getByRole("heading", { level: 1, name: "Second operating account" })).toBeVisible();
  await settings(page, "Company setup");
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
  await expect(terms.getByRole("listitem").filter({ hasText: "Demo Owner B, Aspen Row" })).toContainText(`from ${us(today())}`);

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
  await expect(aspen).toContainText(`Unit 2: Avery Sample: ${us(today())} – Month to month, $1,240.00 a month`);

  // Its leasing fee is half a month's rent, once the owner has the money for it, taken from
  // the lease's ⋯ menu.
  await recordContribution(page, "Demo Owner B", "Aspen Row", "620.00");
  await openLease(page, "Aspen Row, Unit 2");
  const leasing = await leasePanel(page, "Take leasing fee");
  await leasing.getByLabel("From owner").selectOption({ label: "Demo Owner B" });
  await leasing.getByRole("button", { name: "Take leasing fee" }).click();
  await expect(page.getByRole("main")).toContainText("A leasing fee of $620.00 is taken.");
  await expect((await ownerProperty(page, "Demo Owner B", "Aspen Row")).getByRole("cell").nth(2)).toHaveText("$200.00");

  // A second owner of Aspen Row, held in the new operating account.
  await settings(page, "Company setup");
  await aspen.getByText("Add to Aspen Row").click();
  await aspen.getByRole("combobox", { name: "Owner", exact: true }).selectOption({ label: "Demo Owner A" });
  await aspen.getByRole("combobox", { name: "Held in" }).selectOption({ label: "Second operating account" });
  await aspen.getByRole("button", { name: "Add owner" }).click();
  await expect(aspen.getByText(/^Owned by/)).toContainText("Demo Owner A");
  await expect(
    terms.getByRole("combobox", { name: "For" }).getByRole("option", { name: "Demo Owner A, Aspen Row" }),
  ).toHaveCount(1);
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

  // An owner's income statement over last year.
  await section(page, "Reports");
  await page.getByRole("list", { name: "Financial" }).getByRole("link", { name: "Income statement" }).click();
  await page.getByRole("combobox", { name: "Owner" }).selectOption({ label: "Demo Owner B" });
  await page.getByRole("combobox", { name: "Days" }).selectOption({ label: "Last year" });
  const year = new Date().getUTCFullYear() - 1;
  await expect(page.getByLabel("From")).toHaveValue(`${year}-01-01`);
  await expect(page.getByRole("table", { name: "Income statement" })).toContainText("net income");
  await downloads(page, "PDF", PDF);

  // The whole ledger, under Settings.
  await settings(page, "Data export");
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
  await section(page, "Files");
  const keep = async (kind: string, about: string, aboutLabel: string, file: { name: string; mimeType: string; buffer: Buffer }) => {
    await page.getByRole("combobox", { name: "Kind" }).selectOption({ label: kind });
    // A bill or a lease is named with its day; pick it by what it starts with.
    const select = page.getByRole("combobox", { name: about });
    const value = await select.locator("option").filter({ hasText: aboutLabel }).first().getAttribute("value");
    await select.selectOption(value ?? "");
    await page.getByLabel("File").setInputFiles(file);
    await page.getByRole("button", { name: "Upload" }).click();
  };
  const pdf = (text: string) => Buffer.from(`%PDF-1.4\n% ${text}\n`);
  const png = Buffer.from("89504e470d0a1a0a0000000d49484452", "hex");
  await keep("W-9", "Vendor", "Demo Plumbing Co", { name: "w9-plumbing.pdf", mimeType: "application/pdf", buffer: pdf("synthetic W-9") });
  await expect(page.getByText("Uploaded w9-plumbing.")).toBeVisible();
  await keep("Receipt", "Bill", "Demo Roofing Co RF-2207", { name: "roof-receipt.pdf", mimeType: "application/pdf", buffer: pdf("synthetic receipt") });
  await expect(page.getByText("Uploaded roof-receipt.")).toBeVisible();
  await keep("Move-out photo", "Lease", "Aspen Row / Unit 1: Drew Sample", { name: "kitchen.png", mimeType: "image/png", buffer: png });
  await expect(page.getByText("Uploaded kitchen.")).toBeVisible();
  const kept = page.getByRole("list", { name: "Documents" }).getByRole("listitem");
  await expect(kept).toHaveCount(3);

  // A file whose bytes aren't the type its name says is refused, and nothing is kept.
  await page.getByRole("combobox", { name: "Kind" }).selectOption({ label: "W-9" });
  await page.getByLabel("File").setInputFiles({ name: "not-a.pdf", mimeType: "application/pdf", buffer: Buffer.from("plain text") });
  await page.getByRole("button", { name: "Upload" }).click();
  await expect(page.getByRole("alert")).toHaveText("the file isn't a PDF");
  await expect(kept).toHaveCount(3);

  // Removed, it leaves the list (a removal is kept as a row; nothing is deleted).
  await page.getByRole("button", { name: "Remove kitchen" }).click();
  await expect(page.getByText("Removed kitchen.")).toBeVisible();
  await expect(kept).toHaveCount(2);
  const file = await page.request.get((await page.getByRole("link", { name: "w9-plumbing" }).getAttribute("href")) ?? "");
  expect(file.status()).toBe(200);
  expect(Buffer.from(await file.body()).equals(pdf("synthetic W-9"))).toBe(true);

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

  // The demo's expense accounts, and one more.
  await settings(page, "Company setup");
  const accounts = part(page, "Expense accounts");
  await expect(accounts).toContainText("Repairs (Schedule E line 14: Repairs)");
  await accounts.getByLabel("Expense account").fill("Insurance");
  await accounts.getByRole("combobox", { name: "Schedule E line" }).selectOption({ label: "9: Insurance" });
  await accounts.getByRole("button", { name: "Add expense account" }).click();
  await expect(accounts).toContainText("Insurance (Schedule E line 9: Insurance)");

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

  // A policy can't expire before it starts.
  const wrong = await record("other", "OT-0001", daysFromToday(5), daysFromToday(4));
  await expect(wrong.getByRole("alert")).toHaveText("check what you entered and try again");
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
  await expect(panel.getByRole("alert")).toHaveText("check what you entered and try again");
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
