import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { Me } from "../api";
import type { Paged, RentRollRow } from "../rentals";
import { RentPage } from "./RentPage";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  window.history.pushState(null, "", "/");
  window.localStorage.clear();
});

const ME: Me = {
  kind: "staff",
  email: "staff@example.com",
  name: "Staff 1",
  person_id: "s1",
  pmc_id: "p1",
  pmc_name: "Synthetic PMC",
};

const ROW: RentRollRow = {
  id: "l1",
  property: { id: "p1", name: "Property 1" },
  unit: { id: "u1", name: "Unit 1" },
  tenants: ["Tenant 1", "Tenant 2"],
  people: [{ id: "t1", name: "Tenant 1" }, { id: "t2", name: "Tenant 2" }],
  starts_on: "2026-01-01",
  ends_on: null,
  monthly_rent: "1500.00",
  owed: "1550.00",
};
const ROLL: Paged<RentRollRow> = { items: [ROW], total: 1, page: 1, per_page: 50 };

// The API: the rent roll on GET; each POST answers with `answers[path]` (or 201 {}), or 409.
function server(answers: Record<string, unknown> = {}, refuse: Record<string, string> = {}) {
  const fetch = vi.fn(async (path: string, init?: RequestInit) => {
    if (init?.method !== "POST") {
      return new Response(JSON.stringify(answers[path] ?? ROLL), { status: 200 });
    }
    if (path in refuse) return new Response(JSON.stringify({ detail: refuse[path] }), { status: 409 });
    return new Response(JSON.stringify(answers[path] ?? {}), { status: 201 });
  });
  vi.stubGlobal("fetch", fetch);
  return fetch;
}

const posted = (fetch: ReturnType<typeof server>) =>
  fetch.mock.calls
    .filter(([, init]) => init?.method === "POST")
    .map(([path, init]) => [path, JSON.parse(String(init?.body))]);

describe("RentPage", () => {
  async function roll() {
    return screen.findByRole("row", { name: "Property 1, Unit 1" });
  }

  it("lists the leases a page at a time, each linking to its own page", async () => {
    const fetch = server({
      "/api/leases?page=1&show=current": { ...ROLL, total: 51 },
      "/api/property-groups": { groups: [], properties: [{ id: "p1", name: "Property 1" }] },
    });
    render(<RentPage me={ME} />);

    const row = await roll();
    expect(within(row).getAllByRole("cell").map((c) => c.textContent)).toEqual([
      "Property 1", "Unit 1", "Tenant 1, Tenant 2", "$1,500.00", "01/01/2026 – Month to month", "Owes $1,550.00",
    ]);
    expect(within(row).getByRole("link", { name: "Unit 1" }).getAttribute("href")).toBe("/leases/l1");
    expect(within(row).getByTestId("owed").getAttribute("data-tone")).toBe("caution");
    const pages = screen.getByRole("navigation", { name: "Pages" });
    expect(pages.textContent).toContain("1–1 of 51");

    // The next page, one property and the owing leases each ask again, from the first page.
    fireEvent.click(within(pages).getByRole("button", { name: "Next" }));
    await vi.waitFor(() => expect(fetch.mock.calls.at(-1)?.[0]).toBe("/api/leases?page=2&show=current"));
    const picker = screen.getByRole("combobox", { name: "Properties shown" });
    await within(picker).findByRole("option", { name: "Property 1" });
    fireEvent.change(picker, { target: { value: "property:p1" } });
    await vi.waitFor(() =>
      expect(fetch.mock.calls.at(-1)?.[0]).toBe("/api/leases?page=1&show=current&property=p1"),
    );
    fireEvent.change(screen.getByLabelText("Show"), { target: { value: "owing" } });
    await vi.waitFor(() =>
      expect(fetch.mock.calls.at(-1)?.[0]).toBe("/api/leases?page=1&show=owing&property=p1"),
    );
  });

  it("runs the rent and late fees and says how many were charged", async () => {
    const fetch = server({
      "/api/rent/charge-rent": { charged: 3, recurring: 1 },
      "/api/rent/late-fees": { charged: 1 },
    });
    render(<RentPage me={ME} />);
    await roll();

    fireEvent.change(screen.getByLabelText("Rent due on"), { target: { value: "2026-11-01" } });
    fireEvent.click(screen.getByRole("button", { name: "Charge rent" }));
    expect(await screen.findByText("3 rents and 1 recurring charge charged.")).toBeTruthy();
    fireEvent.change(screen.getByLabelText("Late fees as of"), { target: { value: "2026-11-06" } });
    fireEvent.click(screen.getByRole("button", { name: "Assess late fees" }));
    expect(await screen.findByText("1 late fee charged.")).toBeTruthy();

    expect(posted(fetch)).toEqual([
      ["/api/rent/charge-rent", { on: "2026-11-01" }],
      ["/api/rent/late-fees", { on: "2026-11-06" }],
    ]);
  });

  it("says when no lease is listed, and when the leases can't be read", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({ ...ROLL, items: [], total: 0 }), { status: 200 })));
    render(<RentPage me={ME} />);
    expect((await screen.findByTestId("empty-state")).textContent).toBe("No lease is in effect today.");
    cleanup();

    vi.stubGlobal(
      "fetch",
      vi.fn(async () => new Response(JSON.stringify({ detail: "sign in first" }), { status: 401 })),
    );
    render(<RentPage me={ME} />);
    expect((await screen.findAllByRole("alert"))[0]?.textContent).toBe("sign in first");
  });

  it("shows why a run was refused", async () => {
    server({}, { "/api/rent/late-fees": "late fees are assessed as of today or an earlier day" });
    render(<RentPage me={ME} />);
    await roll();

    fireEvent.click(screen.getByRole("button", { name: "Assess late fees" }));

    expect((await screen.findByRole("alert")).textContent).toBe(
      "late fees are assessed as of today or an earlier day",
    );
  });
});
