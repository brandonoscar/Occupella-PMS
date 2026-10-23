import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { Law, Shown } from "../law";
import { LawCard } from "./LawCard";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

// Every rule here is synthetic: none is the law.
const UNKNOWN: Shown = {
  rule: "nsf_fee_cap", label: "Returned payment (NSF) fee", known: false,
  words: "Unknown: no rule of this kind on file for Demo City city, Demo County, California", needs: [], here: null,
  rule_id: null, level: null, jurisdiction_name: null, citation: null, url: null, checked_on: null, reviewed_by: null,
  exceptions: null, effective_from: null, effective_to: null, source: null, confirmed_by: null, confirmed_at: null,
};
const DEPOSIT: Shown = {
  ...UNKNOWN, rule: "deposit_cap", label: "Security deposit", known: true, words: "At most 1 month's rent",
  rule_id: "r1", level: "state", jurisdiction_name: "California", citation: "Synthetic Code 1",
  url: "https://example.org/synthetic-code-1", checked_on: "2026-10-23", reviewed_by: "Synthetic Reviewer",
  exceptions: "Never of a synthetic exception.", effective_from: "2020-01-01", source: "pms",
};
const LAW: Law = {
  day: "2026-10-23",
  jurisdiction: {
    state_fips: "06", state_name: "California", county_fips: "999", county_name: "Demo County", cousub_fips: null,
    cousub_name: null, place_fips: "99990", place_name: "Demo City city", tract: "999900", source: "fake",
    recorded_by: "Staff 1", recorded_at: "2026-10-23T03:00:00Z",
  },
  has_address: true,
  facts: { year_built: 1978, units_in_building: null, owner_occupied: false, owner_kind: null, recorded_by: "Staff 1", recorded_at: "2026-10-23T03:00:00Z" },
  levels: [
    { level: "federal", jurisdiction: "US", name: "United States" },
    { level: "state", jurisdiction: "06", name: "California" },
    { level: "county", jurisdiction: "06999", name: "Demo County" },
    { level: "city", jurisdiction: "0699990", name: "Demo City city" },
  ],
  rules: [DEPOSIT, UNKNOWN],
  note: "Each rule is cited information.",
};

// GETs answer `law`; POSTs answer 204, or 201 with an id for a rule.
function server(law: unknown = LAW) {
  const fetch = vi.fn(async (path: string, init?: RequestInit) => {
    if (init?.method === "POST") {
      if (path === "/api/jurisdiction-rules") return new Response(JSON.stringify({ id: "r2" }), { status: 201 });
      if (path.endsWith("/jurisdiction")) return new Response(JSON.stringify(LAW.jurisdiction), { status: 201 });
      return new Response(null, { status: 204 });
    }
    return new Response(JSON.stringify(law), { status: 200 });
  });
  vi.stubGlobal("fetch", fetch);
  return fetch;
}

const posted = (fetch: ReturnType<typeof server>) =>
  fetch.mock.calls.filter(([, init]) => init?.method === "POST").map(([path, init]) => [path, JSON.parse(String(init?.body))]);

describe("LawCard", () => {
  it("shows where the property is, its facts, and each rule cited or unknown", async () => {
    server();
    render(<LawCard propertyId="p1" again={null} />);
    expect((await screen.findByTestId("law-place")).textContent).toBe(
      "Demo City city, Demo County, California, tract 999900",
    );
    expect(screen.getByText("Found by the stand-in geocoder on 10/23/2026")).toBeTruthy();
    expect(screen.getByText("Year built:").nextSibling?.textContent).toBe("1978");
    expect(screen.getByText("Units in the building:").nextSibling?.textContent).toBe("Not known");
    expect(screen.getByText("An owner lives there:").nextSibling?.textContent).toBe("No");

    const deposit = screen.getByRole("row", { name: "Security deposit" });
    expect(within(deposit).getByText("At most 1 month's rent")).toBeTruthy();
    expect(within(deposit).getByText("Exceptions: Never of a synthetic exception.")).toBeTruthy();
    const cited = within(deposit).getByRole("link", { name: "California: Synthetic Code 1, read 10/23/2026 by Synthetic Reviewer" });
    expect(cited.getAttribute("href")).toBe("https://example.org/synthetic-code-1");
    expect(cited.getAttribute("rel")).toBe("noopener noreferrer");
    expect(within(deposit).getByText("Not confirmed by your company yet")).toBeTruthy();
    const unknown = screen.getByRole("row", { name: "Returned payment (NSF) fee" });
    expect(within(unknown).getByText("Unknown")).toBeTruthy();
    expect(within(unknown).queryByRole("button")).toBeNull();
    expect(screen.getByText("Each rule is cited information.")).toBeTruthy();
  });

  it("confirms a rule and where the property is", async () => {
    const fetch = server();
    render(<LawCard propertyId="p1" again={null} />);
    fireEvent.click(await screen.findByRole("button", { name: "Confirm" }));
    expect(await screen.findByText("Security deposit: confirmed.")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Confirm where it is" }));
    expect(await screen.findByText("Where it is in the law confirmed.")).toBeTruthy();
    expect(posted(fetch)).toEqual([
      ["/api/jurisdiction-rules/r1/confirm", {}],
      ["/api/properties/p1/jurisdiction", {
        county_fips: "999", county_name: "Demo County", cousub_fips: null, cousub_name: null,
        place_fips: "99990", place_name: "Demo City city", tract: "999900",
      }],
    ]);
  });

  it("corrects where it is and keeps the facts, an empty field not known", async () => {
    const fetch = server();
    render(<LawCard propertyId="p1" again={null} />);
    fireEvent.click(await screen.findByRole("button", { name: "Correct where it is" }));
    let panel = screen.getByRole("dialog", { name: "Correct where it is" });
    fireEvent.change(within(panel).getByLabelText("City code (five digits)"), { target: { value: "" } });
    fireEvent.change(within(panel).getByLabelText("City"), { target: { value: "" } });
    fireEvent.click(within(panel).getByRole("button", { name: "Save where it is" }));
    expect(await screen.findByText("Where it is in the law saved.")).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: "Edit facts" }));
    panel = screen.getByRole("dialog", { name: "Edit facts" });
    fireEvent.change(within(panel).getByLabelText("Units in the building"), { target: { value: "4" } });
    fireEvent.change(within(panel).getByLabelText("The owner is"), { target: { value: "person" } });
    fireEvent.change(within(panel).getByLabelText("An owner lives there"), { target: { value: "" } });
    fireEvent.click(within(panel).getByRole("button", { name: "Save facts" }));
    expect(await screen.findByText("Facts saved.")).toBeTruthy();
    expect(posted(fetch)).toEqual([
      ["/api/properties/p1/jurisdiction", {
        county_fips: "999", county_name: "Demo County", cousub_fips: null, cousub_name: null,
        place_fips: null, place_name: null, tract: "999900",
      }],
      ["/api/properties/p1/facts", { year_built: 1978, units_in_building: 4, owner_occupied: null, owner_kind: "person" }],
    ]);
  });

  it("adds a rule read in a statute, the lesser of two numbers, for properties built before a year", async () => {
    const fetch = server();
    render(<LawCard propertyId="p1" again={null} />);
    fireEvent.click(await screen.findByRole("button", { name: "Add a rule" }));
    const panel = screen.getByRole("dialog", { name: "Add a rule" });
    fireEvent.change(within(panel).getByLabelText("Of"), { target: { value: "city" } });
    fireEvent.change(within(panel).getByLabelText("Rule"), { target: { value: "late_fee_cap" } });
    fireEvent.change(within(panel).getByLabelText("Number"), { target: { value: "50" } });
    fireEvent.change(within(panel).getByLabelText("Or a second number"), { target: { value: "lesser" } });
    fireEvent.change(within(panel).getByLabelText("Second number"), { target: { value: "5" } });
    fireEvent.change(within(panel).getByLabelText("Its unit"), { target: { value: "percent_of_rent" } });
    fireEvent.change(within(panel).getByLabelText("In force from"), { target: { value: "2026-01-01" } });
    fireEvent.change(within(panel).getByLabelText("Citation"), { target: { value: "Synthetic Code 9" } });
    fireEvent.change(within(panel).getByLabelText("The statute's page (https)"), { target: { value: "https://example.org/9" } });
    fireEvent.change(within(panel).getByLabelText("Read in the statute on"), { target: { value: "2026-10-23" } });
    fireEvent.change(within(panel).getByLabelText("Read by"), { target: { value: "Synthetic Reviewer" } });
    fireEvent.change(within(panel).getByLabelText("Built before (year)"), { target: { value: "1979" } });
    fireEvent.click(within(panel).getByRole("button", { name: "Add the rule" }));
    expect(await screen.findByText("Rule added and confirmed.")).toBeTruthy();
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(posted(fetch)).toEqual([["/api/jurisdiction-rules", {
      property_id: "p1", level: "city", rule: "late_fee_cap", value_kind: "number", value_number: "50",
      value_unit: "dollars", combine: "lesser", also_number: "5", also_unit: "percent_of_rent", value_words: null,
      built_before: 1979, built_on_or_after: null, units_at_least: null, units_at_most: null, owner_occupied: null,
      owner_kind: null, precedence: 0, effective_from: "2026-01-01", effective_to: null, citation: "Synthetic Code 9",
      url: "https://example.org/9", checked_on: "2026-10-23", reviewed_by: "Synthetic Reviewer", exceptions: null,
      supersedes: null,
    }]]);
  });

  it("corrects a rule in its own place and kind, naming what it corrects", async () => {
    const fetch = server();
    render(<LawCard propertyId="p1" again={null} />);
    fireEvent.click(await screen.findByRole("button", { name: "Correct" }));
    const panel = screen.getByRole("dialog", { name: "Correct: Security deposit" });
    expect((within(panel).getByLabelText("Of") as HTMLSelectElement).value).toBe("state");
    expect((within(panel).getByLabelText("Rule") as HTMLSelectElement).disabled).toBe(true);
    fireEvent.change(within(panel).getByLabelText("Its value"), { target: { value: "words" } });
    fireEvent.change(within(panel).getByLabelText("The rule in words"), { target: { value: "As the lease says." } });
    fireEvent.change(within(panel).getByLabelText("Citation"), { target: { value: "Synthetic Code 2" } });
    fireEvent.change(within(panel).getByLabelText("Read by"), { target: { value: "Synthetic Reviewer" } });
    fireEvent.change(within(panel).getByLabelText("The statute's page (https)"), { target: { value: "https://example.org/2" } });
    fireEvent.click(within(panel).getByRole("button", { name: "Correct the rule" }));
    expect(await screen.findByText("Rule corrected.")).toBeTruthy();
    const body = posted(fetch)[0]?.[1];
    expect(body).toMatchObject({ level: "state", rule: "deposit_cap", value_kind: "words", value_number: null,
      value_unit: null, value_words: "As the lease says.", supersedes: "r1" });
  });

  it("asks for the address first, and says where it isn't found yet", async () => {
    server({ ...LAW, jurisdiction: null, has_address: false, rules: [] });
    render(<LawCard propertyId="p1" again={null} />);
    expect((await screen.findByTestId("law-place")).textContent).toBe("Add the property's address first.");
    expect(screen.queryByRole("button", { name: "Say where it is" })).toBeNull();
    cleanup();
    server({ ...LAW, jurisdiction: null, has_address: true, rules: [] });
    render(<LawCard propertyId="p1" again={null} />);
    expect((await screen.findByTestId("law-place")).textContent).toContain("isn't on file yet");
    expect(screen.getByRole("button", { name: "Say where it is" })).toBeTruthy();
  });
});
