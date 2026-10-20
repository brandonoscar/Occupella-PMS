import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { Me } from "../api";
import { STAFF_NAV } from "../pages/staffNav";
import { Card, isCurrent, PageHeader, Shell } from "./Shell";

beforeEach(() => window.history.pushState(null, "", "/settings/staff"));

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

const STAFF: Me = {
  kind: "staff", email: "manager@example.com", name: "Manager 1", person_id: "s1", pmc_id: "p1",
  pmc_name: "Synthetic PMC", roles: ["admin"],
};
const OWNER: Me = { ...STAFF, kind: "owner", name: "Owner 1", roles: [] };

const sections = () => screen.getByRole("navigation", { name: "Sections" });

describe("Shell", () => {
  it("lays the page out as Occupella does: a left sidebar, then a bar and the page", () => {
    render(<Shell me={STAFF} nav={STAFF_NAV}><p>Page body</p></Shell>);

    const sidebar = screen.getByRole("complementary", { name: "Sidebar" });
    expect(within(sidebar).getByText("occupella")).toBeTruthy();
    // Who is signed in, and where, is the avatar at the right of the bar, as in Occupella.
    const avatar = within(screen.getByRole("banner")).getByRole("img", { name: "Manager 1, Synthetic PMC" });
    expect(avatar.textContent).toBe("M");
    expect(within(screen.getByRole("main")).getByText("Page body")).toBeTruthy();
    // The sidebar comes first, the content beside it.
    expect(sidebar.compareDocumentPosition(screen.getByRole("main")) & Node.DOCUMENT_POSITION_FOLLOWING)
      .toBeTruthy();
  });

  it("lists staff's menu, each group's pages under its name, and marks the one shown", () => {
    render(<Shell me={STAFF} nav={STAFF_NAV}><p /></Shell>);

    const links = within(sections()).getAllByRole("link");
    expect(links.map((link) => link.textContent)).toEqual([
      "Dashboard", "Properties", "Rent roll", "Tenants", "Owners", "Housing authorities", "Move-outs", "Listings", "Leads", "Applicants", "Draft leases", "Renewals", "Banking", "Bills", "Utility billing",
      "General ledger", "Pay owners", "Management fees", "Budgets", "Work orders", "Vendors", "Files", "Reports", "Settings",
    ]);
    const groups = within(sections()).getAllByRole("group");
    expect(groups.map((group) => group.getAttribute("aria-label"))).toEqual([
      "Rentals", "Leasing", "Accounting", "Maintenance",
    ]);
    const accounting = within(sections()).getByRole("group", { name: "Accounting" });
    expect(within(accounting).getAllByRole("link").map((link) => link.textContent)).toEqual([
      "Banking", "Bills", "Utility billing", "General ledger", "Pay owners", "Management fees", "Budgets",
    ]);
    // Each group and each page outside one has its icon.
    expect(groups.every((group) => group.querySelector("svg"))).toBe(true);
    for (const name of ["Dashboard", "Files", "Reports", "Settings"]) {
      expect(within(sections()).getByRole("link", { name }).querySelector("svg")).toBeTruthy();
    }
    // A page of Settings is Settings shown.
    const current = within(sections()).getByRole("link", { name: "Settings" });
    expect(current.getAttribute("aria-current")).toBe("page");
    expect(current.className).toContain("bg-chrome-raised");
    expect(within(sections()).getByRole("link", { name: "Dashboard" }).getAttribute("aria-current")).toBeNull();
  });

  it("marks the Dashboard only on its own page, and a page also on its own pages", () => {
    expect([isCurrent("/", "/"), isCurrent("/", "/bills")]).toEqual([true, false]);
    expect([isCurrent("/settings", "/settings/staff"), isCurrent("/bills", "/bills-due")]).toEqual([true, false]);
  });

  it("goes to a section in place, and leaves a new-tab click to the browser", () => {
    render(<Shell me={STAFF} nav={STAFF_NAV}><p /></Shell>);

    fireEvent.click(within(sections()).getByRole("link", { name: "Bills" }));
    expect(window.location.pathname).toBe("/bills");
    expect(within(sections()).getByRole("link", { name: "Bills" }).getAttribute("aria-current")).toBe("page");

    const rent = within(sections()).getByRole("link", { name: "Rent roll" });
    expect(fireEvent.click(rent, { ctrlKey: true })).toBe(true); // not prevented
    expect(window.location.pathname).toBe("/bills");
  });

  it("gives staff the search in the bar, and nobody else", () => {
    render(<Shell me={STAFF} nav={STAFF_NAV}><p /></Shell>);
    expect(within(screen.getByRole("banner")).getByLabelText("Search people, homes, bills, work orders and files")).toBeTruthy();
    cleanup();

    render(<Shell me={OWNER}><p /></Shell>);
    expect(screen.queryByLabelText("Search people, homes, bills, work orders and files")).toBeNull();
  });

  it("names an owner's or a tenant's one page in the rail", () => {
    window.history.pushState(null, "", "/");
    render(<Shell me={OWNER}><p /></Shell>);

    const links = within(sections()).getAllByRole("link");
    expect(links.map((link) => [link.textContent, link.getAttribute("aria-current")])).toEqual([
      ["Overview", "page"],
    ]);
  });

  it("signs out from the sidebar, back to the PMC's sign-in page", async () => {
    const fetch = vi.fn(async (_path: string) => new Response(null, { status: 204 }));
    vi.stubGlobal("fetch", fetch);
    render(<Shell me={STAFF} nav={STAFF_NAV}><p /></Shell>);

    fireEvent.click(within(screen.getByRole("complementary")).getByRole("button", { name: "Sign out" }));

    await waitFor(() => expect(window.location.pathname + window.location.search).toBe("/sign-in?pmc=p1"));
    expect(fetch.mock.calls[0]?.[0]).toBe("/api/sign-out");
  });

  it("opens the sidebar as a drawer from the bar, and closes it", () => {
    render(<Shell me={STAFF} nav={STAFF_NAV}><p /></Shell>);
    const sidebar = screen.getByRole("complementary", { name: "Sidebar" });
    expect(sidebar.getAttribute("data-open")).toBe("false");

    fireEvent.click(screen.getByRole("button", { name: "Open menu" }));
    expect(sidebar.getAttribute("data-open")).toBe("true");
    fireEvent.keyDown(window, { key: "Escape" });
    expect(sidebar.getAttribute("data-open")).toBe("false");

    fireEvent.click(screen.getByRole("button", { name: "Open menu" }));
    fireEvent.click(screen.getByTestId("scrim"));
    expect(sidebar.getAttribute("data-open")).toBe("false");

    fireEvent.click(screen.getByRole("button", { name: "Open menu" }));
    fireEvent.click(screen.getByRole("button", { name: "Close menu" }));
    expect(sidebar.getAttribute("data-open")).toBe("false");

    // Choosing a section closes it too.
    fireEvent.click(screen.getByRole("button", { name: "Open menu" }));
    fireEvent.click(within(sections()).getByRole("link", { name: "Owners" }));
    expect(sidebar.getAttribute("data-open")).toBe("false");
  });

  it("keeps a closed drawer out of the tab order on a narrow screen, and only there", () => {
    const listeners: ((event: MediaQueryListEvent) => void)[] = [];
    const matchMedia = (matches: boolean) =>
      vi.fn(() => ({
        matches,
        addEventListener: (_: string, listener: (event: MediaQueryListEvent) => void) =>
          listeners.push(listener),
        removeEventListener: () => undefined,
      }));
    vi.stubGlobal("matchMedia", matchMedia(true));
    render(<Shell me={STAFF} nav={STAFF_NAV}><p /></Shell>);
    const sidebar = screen.getByRole("complementary", { hidden: true });
    expect(sidebar.hasAttribute("inert")).toBe(true);
    fireEvent.click(screen.getByRole("button", { name: "Open menu" }));
    expect(sidebar.hasAttribute("inert")).toBe(false);
    cleanup();

    vi.stubGlobal("matchMedia", matchMedia(false));
    render(<Shell me={STAFF} nav={STAFF_NAV}><p /></Shell>);
    expect(screen.getByRole("complementary").hasAttribute("inert")).toBe(false);
  });
});

describe("PageHeader", () => {
  it("is the page's title, one line on what it is for, and its actions", () => {
    render(<PageHeader title="Rent" description="Each lease's charges." actions={<button type="button">Run</button>} />);
    expect(screen.getByRole("heading", { level: 1, name: "Rent" }).className).toContain("font-display");
    expect(screen.getByText("Each lease's charges.")).toBeTruthy();
    expect(screen.getByRole("button", { name: "Run" })).toBeTruthy();
  });
});

describe("Card", () => {
  it("is a part of the page under a small uppercase label, with no box around it", () => {
    render(<Card title="Coming up"><p>Rent</p></Card>);
    const heading = screen.getByRole("heading", { level: 2, name: "Coming up" });
    expect(heading.className).toContain("uppercase");
    const section = heading.closest("section") as HTMLElement;
    expect(section.className).not.toContain("border");
    expect(within(section).getByText("Rent")).toBeTruthy();
  });
});
