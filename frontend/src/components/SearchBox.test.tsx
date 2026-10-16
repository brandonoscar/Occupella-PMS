import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { Found } from "../rentals";
import { SearchBox } from "./SearchBox";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  window.history.pushState(null, "", "/");
});

const FOUND: Found[] = [
  { kind: "property", id: "p1", label: "Property 1", detail: "Owner 1", href: "/properties/p1" },
  { kind: "unit", id: "u1", label: "Unit 1", detail: "Property 1", href: "/units/u1" },
  { kind: "tenant", id: "t1", label: "Tenant 1", detail: "", href: "/leases/l1" },
];

function server(found: Found[] = FOUND) {
  const fetch = vi.fn(async (_path: string) => new Response(JSON.stringify(found), { status: 200 }));
  vi.stubGlobal("fetch", fetch);
  return fetch;
}

const box = () => screen.getByLabelText("Search properties, units and tenants");

describe("SearchBox", () => {
  it("asks from two letters and lists what it found, each with its kind", async () => {
    const fetch = server();
    render(<SearchBox />);

    fireEvent.change(box(), { target: { value: "P" } });
    await new Promise((done) => setTimeout(done, 200));
    expect(fetch).not.toHaveBeenCalled();

    fireEvent.change(box(), { target: { value: " Pr " } });
    const results = await screen.findByRole("list", { name: "Search results" });
    expect(fetch.mock.calls.map(([path]) => path)).toEqual(["/api/search?q=Pr"]);
    expect(within(results).getAllByRole("link").map((link) => [link.textContent, link.getAttribute("href")])).toEqual([
      ["Property 1 · Owner 1Property", "/properties/p1"],
      ["Unit 1 · Property 1Unit", "/units/u1"],
      ["Tenant 1Tenant", "/leases/l1"],
    ]);
  });

  it("opens a result's page, and Enter opens the first", async () => {
    server();
    render(<SearchBox />);

    fireEvent.change(box(), { target: { value: "Unit" } });
    fireEvent.click(await screen.findByRole("link", { name: /Unit 1/ }));
    expect(window.location.pathname).toBe("/units/u1");
    expect((box() as HTMLInputElement).value).toBe("");
    expect(screen.queryByRole("list", { name: "Search results" })).toBeNull();

    fireEvent.change(box(), { target: { value: "Prop" } });
    await screen.findByRole("list", { name: "Search results" });
    fireEvent.keyDown(box(), { key: "Enter" });
    expect(window.location.pathname).toBe("/properties/p1");
  });

  it("leaves a new-tab click to the browser, clears on Escape, and lists nothing on a failure", async () => {
    server();
    render(<SearchBox />);

    fireEvent.change(box(), { target: { value: "Tenant" } });
    const link = await screen.findByRole("link", { name: /Tenant 1/ });
    expect(fireEvent.click(link, { ctrlKey: true })).toBe(true); // not prevented
    expect(window.location.pathname).toBe("/");
    fireEvent.keyDown(box(), { key: "Escape" });
    expect((box() as HTMLInputElement).value).toBe("");
    await vi.waitFor(() => expect(screen.queryByRole("list", { name: "Search results" })).toBeNull());

    vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({ detail: "sign in first" }), { status: 401 })));
    fireEvent.change(box(), { target: { value: "Tenant" } });
    await new Promise((done) => setTimeout(done, 250));
    expect(screen.queryByRole("list", { name: "Search results" })).toBeNull();
  });
});
