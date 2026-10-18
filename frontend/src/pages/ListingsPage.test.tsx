import { cleanup, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ListingsPage } from "./ListingsPage";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  window.history.replaceState(null, "", "/");
});

function server(answer: unknown, status = 200) {
  const fetch = vi.fn(async (_path: string) => new Response(JSON.stringify(answer), { status }));
  vi.stubGlobal("fetch", fetch);
  return fetch;
}

describe("ListingsPage", () => {
  it("shows a PMC's listed units, each with the way to apply", async () => {
    window.history.replaceState(null, "", "/listings?pmc=p1");
    const fetch = server({
      pmc: "Synthetic PMC",
      listings: [
        { id: "v1", property: "Property 1", unit: "Unit 1", monthly_rent: "1250.50", description: null, listed_at: "2026-10-13T00:00:00Z", photos: [] },
        { id: "v2", property: "Property 1", unit: "Unit 2", monthly_rent: "1500.00", description: "Two bedrooms,\nsunny.", listed_at: "2026-10-13T00:00:00Z", photos: ["d1", "d2"] },
      ],
    });
    render(<ListingsPage />);

    expect(await screen.findByRole("heading", { name: "Homes for rent from Synthetic PMC" })).toBeTruthy();
    const second = screen.getByRole("listitem", { name: "Property 1, Unit 2" });
    expect(within(second).getByText("$1,500.00 a month")).toBeTruthy();
    expect(within(second).getByText(/Two bedrooms/)).toBeTruthy();
    expect(within(second).getByRole("link", { name: "Apply" }).getAttribute("href")).toBe("/apply?listing=v2");
    const first = screen.getByRole("listitem", { name: "Property 1, Unit 1" });
    expect(within(first).getByRole("link", { name: "Apply" }).getAttribute("href")).toBe("/apply?listing=v1");
    // A unit's photos, in order, from the public page's own address for them; none, no list.
    const photos = within(second).getByRole("list", { name: "Photos, Property 1, Unit 2" });
    expect(within(photos).getAllByRole("img").map((img) => [img.getAttribute("alt"), img.getAttribute("src")])).toEqual([
      ["Property 1, Unit 2, photo 1", "/api/listings/p1/photos/d1"],
      ["Property 1, Unit 2, photo 2", "/api/listings/p1/photos/d2"],
    ]);
    expect(within(first).queryByRole("list")).toBeNull();
    expect(fetch.mock.calls[0]?.[0]).toBe("/api/listings/p1");
  });

  it("says when nothing is available", async () => {
    window.history.replaceState(null, "", "/listings?pmc=p1");
    server({ pmc: "Synthetic PMC", listings: [] });
    render(<ListingsPage />);

    expect(await screen.findByText("Nothing is available right now.")).toBeTruthy();
    // An empty page says so with Occupella's empty state, under the page's own title.
    expect(screen.getByTestId("empty-state").textContent).toContain("Nothing is available right now.");
    expect(screen.getByRole("heading", { level: 1 }).textContent).toMatch(/^Homes for rent from /);
  });

  it("says what went wrong for an unknown PMC or a page with no PMC", async () => {
    window.history.replaceState(null, "", "/listings?pmc=nobody");
    server({ detail: "no such PMC" }, 404);
    render(<ListingsPage />);
    expect(await screen.findByText("no such PMC")).toBeTruthy();
    cleanup();

    window.history.replaceState(null, "", "/listings");
    const fetch = server({});
    render(<ListingsPage />);
    expect(await screen.findByText("This page needs the property manager's link.")).toBeTruthy();
    expect(fetch).not.toHaveBeenCalled();
  });
});
