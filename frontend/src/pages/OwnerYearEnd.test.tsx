import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { YearEndData } from "../yearEnd";
import { OwnerYearEnd } from "./OwnerYearEnd";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.useRealTimers();
});

const answer = (body: unknown, status = 200) =>
  vi.fn(async (_path: string) => new Response(JSON.stringify(body), { status }));

const part = (line: number, property: string | null, item: string, amount: string) => [
  String(line), property, item, amount,
];
const PACKAGE: YearEndData = {
  title: "Year-end package, 2025",
  columns: ["line", "property", "item", "amount"],
  rows: [
    ["1", "Synthetic PMC", "PMC", null],
    ["2", "Owner 1", "owner", null],
    ["3", "2025, January 1 to December 31 UTC", "year", null],
    part(4, "Property 1", "collected from tenants", "1500.00"),
    part(5, "Property 2", "collected from tenants", "900.00"),
    part(6, null, "all properties: collected from tenants", "2400.00"),
    part(7, null, "1099-MISC rents (box 1)", "2400.00"),
  ],
};

describe("OwnerYearEnd", () => {
  it("shows last year's package by property, the totals and the rents for the 1099-MISC", async () => {
    vi.useFakeTimers({ toFake: ["Date"], now: new Date("2026-03-01T12:00:00Z") });
    const fetch = answer(PACKAGE);
    vi.stubGlobal("fetch", fetch);
    render(<OwnerYearEnd />);

    const second = await screen.findByRole("table", { name: "Property 2" });
    expect(within(second).getByText("Collected from tenants")).toBeTruthy();
    expect(second.textContent).toContain("$900.00");
    expect(screen.getByRole("table", { name: "All properties" }).textContent).toContain("$2,400.00");
    expect(screen.getByTestId("rents").textContent).toBe("Rents for your 1099-MISC (box 1): $2,400.00");
    expect(fetch.mock.calls[0]?.[0]).toBe("/api/owner/year-end?year=2025");
    expect(screen.getByRole("link", { name: "PDF" }).getAttribute("href")).toBe(
      "/api/owner/year-end/download?year=2025&format=pdf",
    );
    expect(screen.getByRole("link", { name: "The year's statement" }).getAttribute("href")).toBe(
      "/api/owner/statement/download?start=2025-01-01&end=2025-12-31&format=pdf",
    );
  });

  it("shows another year, and says why one can't be read", async () => {
    const fetch = answer(PACKAGE);
    vi.stubGlobal("fetch", fetch);
    render(<OwnerYearEnd />);
    await screen.findByRole("table", { name: "Property 1" });

    fireEvent.change(screen.getByLabelText("Year"), { target: { value: "2024" } });
    fireEvent.click(screen.getByRole("button", { name: "Show" }));
    await vi.waitFor(() => expect(fetch.mock.calls.at(-1)?.[0]).toBe("/api/owner/year-end?year=2024"));
    cleanup();

    vi.stubGlobal("fetch", answer({ detail: "sign in first" }, 401));
    render(<OwnerYearEnd />);
    expect((await screen.findAllByRole("alert"))[0]?.textContent).toBe("sign in first");
  });
});
