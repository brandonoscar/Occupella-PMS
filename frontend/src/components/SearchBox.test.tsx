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
  { kind: "tenant", id: "t1", label: "Tenant 1", detail: "", href: "/tenants/t1" },
  { kind: "owner", id: "o1", label: "Owner 1", detail: "", href: "/owners/o1" },
];

function server(found: Found[] = FOUND) {
  const fetch = vi.fn(async (_path: string) => new Response(JSON.stringify(found), { status: 200 }));
  vi.stubGlobal("fetch", fetch);
  return fetch;
}

const box = () => screen.getByLabelText("Search people, homes, bills, work orders and files");

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
      ["Tenant 1Tenant", "/tenants/t1"],
      ["Owner 1Owner", "/owners/o1"],
    ]);
  });

  it("names each kind it finds beyond homes and people", async () => {
    server([
      { kind: "vendor", id: "v1", label: "Vendor 1", detail: "Plumbing", href: "/vendors/v1" },
      { kind: "bill", id: "b1", label: "Vendor 1 INV-1", detail: "Property 1 · Repair · due 10/20/2026", href: "/bills/b1" },
      { kind: "work order", id: "w1", label: "Leak", detail: "Unit 1, Property 1", href: "/work-orders/w1" },
      { kind: "applicant", id: "a1", label: "Applicant 1", detail: "Unit 2, Property 1", href: "/applicants/a1" },
      { kind: "lead", id: "l1", label: "Lead 1", detail: "any home", href: "/leads/l1" },
      { kind: "file", id: "f1", label: "W-9", detail: "10/20/2026", href: "/vendors/v1" },
    ]);
    render(<SearchBox />);

    fireEvent.change(box(), { target: { value: "in" } });
    const results = await screen.findByRole("list", { name: "Search results" });
    expect(within(results).getAllByRole("link").map((link) => link.lastElementChild?.textContent)).toEqual([
      "Vendor", "Bill", "Work order", "Applicant", "Lead", "File",
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

  it("moves from the box through the results with the arrow keys, and Escape goes back to the box", async () => {
    server();
    render(<SearchBox />);
    fireEvent.change(box(), { target: { value: "Pr" } });
    const results = await screen.findByRole("list", { name: "Search results" });
    const links = within(results).getAllByRole("link");

    fireEvent.keyDown(box(), { key: "ArrowDown" });
    expect(document.activeElement).toBe(links[0]);
    fireEvent.keyDown(links[0]!, { key: "ArrowDown" });
    expect(document.activeElement).toBe(links[1]);
    fireEvent.keyDown(links[1]!, { key: "ArrowUp" });
    fireEvent.keyDown(links[0]!, { key: "ArrowUp" });
    expect(document.activeElement).toBe(box());

    fireEvent.keyDown(box(), { key: "ArrowDown" });
    fireEvent.keyDown(links[0]!, { key: "Escape" });
    expect(document.activeElement).toBe(box());
    expect((box() as HTMLInputElement).value).toBe("");
  });
});
