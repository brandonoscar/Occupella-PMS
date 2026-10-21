// The owner and tenant portals on a phone (the founder's review q124): a 390 x 844 screen, as an
// iPhone 12 to 15 shows a page, touch and all. Below tablet width the menu folds behind a menu
// button; nothing on a page runs off the side of the screen (a wide table scrolls inside its own
// box instead); every page is checked against WCAG 2.2 AA by axe, as on a desktop; and a
// screenshot of each page is kept with the run. Each test uses its own demo PMC (e2e/seed.py,
// "phone").
import { readFileSync } from "node:fs";
import { expect, type Page, type TestInfo, test } from "@playwright/test";
import { expectAccessible } from "./a11y";

const seed: { features: Record<string, { owner_a: string; taylor: string }> } = JSON.parse(
  readFileSync(new URL("./.seed.json", import.meta.url), "utf8"),
);
const PASSWORD = "a long synthetic passphrase";

test.use({ viewport: { width: 390, height: 844 }, deviceScaleFactor: 3, isMobile: true, hasTouch: true });

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
  expect(await page.evaluate(() => (window as unknown as { cspViolations: string[] }).cspViolations)).toEqual([]);
  await expectAccessible(page);
});

// An owner or a tenant takes their invite on the phone: a password, and they are in.
async function join(page: Page, invite: string, heading: string): Promise<void> {
  await page.goto(`/invite#${invite}`);
  await page.getByLabel("Choose a password (12 characters or more)").fill(PASSWORD);
  await page.getByLabel("The same password again").fill(PASSWORD);
  await page.getByRole("button", { name: "Set password" }).tap();
  await expect(page.getByRole("heading", { level: 1, name: heading })).toBeVisible();
}

// What runs off the right of the screen, outside a box that scrolls sideways on its own: each as
// "tag text", so a failure says what to fix. Nothing should.
async function offTheSide(page: Page): Promise<string[]> {
  return page.evaluate(() => {
    const width = document.documentElement.clientWidth;
    const scrollsOnItsOwn = (el: Element | null): boolean => {
      for (let at = el?.parentElement; at; at = at.parentElement) {
        const x = getComputedStyle(at).overflowX;
        if (x === "auto" || x === "scroll") return true;
      }
      return false;
    };
    return [...document.querySelectorAll("main *, header *")]
      .filter((el) => {
        const box = el.getBoundingClientRect();
        return box.width > 0 && box.right > width + 1 && !scrollsOnItsOwn(el);
      })
      .map((el) => `${el.tagName.toLowerCase()} ${(el.textContent ?? "").trim().slice(0, 40)}`);
  });
}

async function looksRightOnAPhone(page: Page, info: TestInfo, name: string): Promise<void> {
  expect(await offTheSide(page), `off the side of the screen on ${name}`).toEqual([]);
  // The page itself never scrolls sideways.
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= document.documentElement.clientWidth)).toBe(true);
  await info.attach(`${name} on a phone`, { body: await page.screenshot({ fullPage: true }), contentType: "image/png" });
}

// The menu folds behind its button: closed, its links can't be reached; opened, they can, and it
// closes again.
async function theMenuFolds(page: Page): Promise<void> {
  const nav = page.getByRole("navigation", { name: "Sections" });
  await expect(nav).not.toBeInViewport();
  await page.getByRole("button", { name: "Open menu" }).tap();
  await expect(nav).toBeInViewport();
  await expect(page.getByRole("button", { name: "Sign out" })).toBeInViewport();
  await page.getByRole("button", { name: "Close menu" }).tap();
  await expect(nav).not.toBeInViewport();
}

test("an owner reads their properties and statement on a phone", async ({ page }, info) => {
  await join(page, seed.features.phone!.owner_a, "Your properties");
  await theMenuFolds(page);
  await expect(page.getByRole("row", { name: /^Birch Flats/ })).toContainText("$250.00");
  // The statement's table for a property scrolls sideways in its own frame, which takes focus
  // so the keyboard can scroll it too.
  const birch = page.getByRole("table", { name: "Birch Flats" }).first();
  await birch.scrollIntoViewIfNeeded();
  await expect(birch).toBeVisible();
  await expect(birch.locator("xpath=..")).toHaveAttribute("tabindex", "0");
  await looksRightOnAPhone(page, info, "the owner's page");
});

test("a tenant reads their lease and what they owe, and files a repair request on a phone", async ({ page }, info) => {
  await join(page, seed.features.phone!.taylor, "Welcome, Taylor Demo");
  await theMenuFolds(page);
  await expect(page.getByTestId("balance-due")).toBeVisible();
  await looksRightOnAPhone(page, info, "the tenant's page");

  // A repair request, typed and sent with the phone's keyboard and touch.
  const request = page.locator("section", { has: page.getByRole("heading", { level: 2, name: "Maintenance requests" }) });
  await request.getByLabel("What needs fixing").fill("The kitchen tap drips");
  await request.getByRole("button", { name: "Send request" }).tap();
  await expect(request).toContainText("The kitchen tap drips");
  await looksRightOnAPhone(page, info, "the tenant's page after a request");
});
