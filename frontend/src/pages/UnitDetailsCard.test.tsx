import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { MarketRent, UnitDetailsCard, UnitSize } from "./UnitDetailsCard";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

const NONE = { bedrooms: null, bathrooms: null, square_feet: null, market_rent: null };
const TWO_BED = { bedrooms: 2, bathrooms: "1.5", square_feet: 1100, market_rent: "1250.00" };

function server(answer: unknown, status = 200) {
  const fetch = vi.fn(async (_path: string, _init?: RequestInit) => new Response(JSON.stringify(answer), { status }));
  vi.stubGlobal("fetch", fetch);
  return fetch;
}

function details() {
  return within(screen.getByLabelText("Unit details")).getAllByRole("definition").map((d) => d.textContent);
}

describe("UnitDetailsCard", () => {
  it("shows what is recorded, and says what isn't", () => {
    render(<UnitDetailsCard unitId="u1" details={TWO_BED} refresh={() => {}} />);
    expect(details()).toEqual(["2", "1.5", "1,100", "$1,250.00"]);
    cleanup();
    render(<UnitDetailsCard unitId="u1" details={{ ...NONE, bedrooms: 0 }} refresh={() => {}} />);
    expect(details()).toEqual(["Studio", "Not recorded", "Not recorded", "Not recorded"]);
  });

  it("edits the details in a side panel and says what the save did", async () => {
    const fetch = server({ changed: true });
    const refresh = vi.fn();
    render(<UnitDetailsCard unitId="u1" details={TWO_BED} refresh={refresh} />);

    fireEvent.click(screen.getByRole("button", { name: "Edit details" }));
    const form = screen.getByRole("dialog", { name: "Edit details" });
    expect((within(form).getByLabelText("Market rent a month") as HTMLInputElement).value).toBe("1250.00");
    fireEvent.change(within(form).getByLabelText("Bedrooms"), { target: { value: "3" } });
    fireEvent.change(within(form).getByLabelText("Square feet"), { target: { value: "1,250" } });
    fireEvent.change(within(form).getByLabelText("Market rent a month"), { target: { value: "$1,395" } });
    fireEvent.change(within(form).getByLabelText("Bathrooms"), { target: { value: "" } });
    fireEvent.click(within(form).getByRole("button", { name: "Save details" }));

    expect(await screen.findByText("Details saved.")).toBeTruthy();
    expect(fetch.mock.calls.map(([path, init]) => [path, JSON.parse(String(init?.body))])).toEqual([
      ["/api/units/u1/details", { bedrooms: "3", bathrooms: null, square_feet: "1250", market_rent: "1395" }],
    ]);
    expect(refresh).toHaveBeenCalled();
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("says when the same details were saved again, and why a save was refused", async () => {
    server({ changed: false });
    render(<UnitDetailsCard unitId="u1" details={TWO_BED} refresh={() => {}} />);
    fireEvent.click(screen.getByRole("button", { name: "Edit details" }));
    fireEvent.click(screen.getByRole("button", { name: "Save details" }));
    expect(await screen.findByText("Nothing changed.")).toBeTruthy();

    server({ detail: "bathrooms count in halves: 1, 1.5, 2" }, 422);
    fireEvent.click(screen.getByRole("button", { name: "Edit details" }));
    const form = screen.getByRole("dialog", { name: "Edit details" });
    fireEvent.change(within(form).getByLabelText("Bathrooms"), { target: { value: "1.25" } });
    fireEvent.click(within(form).getByRole("button", { name: "Save details" }));
    expect((await within(form).findByRole("alert")).textContent).toContain("bathrooms count in halves");
    expect(screen.getByRole("dialog", { name: "Edit details" })).toBeTruthy();
  });
});

describe("UnitSize and MarketRent", () => {
  it("read a unit's size under its name and its market rent in a cell, or nothing", () => {
    render(<table><tbody><tr><td><UnitSize details={TWO_BED} /></td><td><MarketRent details={TWO_BED} /></td></tr></tbody></table>);
    expect(screen.getByTestId("unit-size").textContent).toBe("2 beds, 1.5 baths, 1,100 sq ft");
    expect(screen.getByText("$1,250.00")).toBeTruthy();
    cleanup();
    const { container } = render(<div><UnitSize details={NONE} /><MarketRent details={NONE} /></div>);
    expect(container.textContent).toBe("");
  });
});
