import { act, cleanup, render, renderHook, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { Checked, Shown } from "../law";
import { checkQuery, citedLine, confirmedLine, factLines, placeLine, useLawCheck } from "../law";
import { RulesBeside } from "./RulesBeside";

afterEach(() => {
  cleanup();
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

// Synthetic rules: none is the law.
const CAP: Shown = {
  rule: "late_fee_cap", label: "Late fee", known: true, words: "At most $50.00", needs: [],
  here: null, rule_id: "r1", level: "state", jurisdiction_name: "California", citation: "Synthetic Code 1",
  url: "https://example.org/1", checked_on: "2026-10-23", reviewed_by: "Synthetic Reviewer", exceptions: null,
  effective_from: "2020-01-01", effective_to: null, source: "staff", confirmed_by: "Staff 1", confirmed_at: "2026-10-23T00:00:00Z",
};
const WAIT: Shown = {
  ...CAP, rule: "late_fee_grace_days", label: "Days before a late fee", known: false,
  words: "Unknown until the year built is on file for the property", needs: ["year_built"], citation: null, url: null,
  confirmed_by: null,
};
const CHECKED: Checked = {
  rules: [WAIT, CAP],
  warnings: [{ rule: "late_fee_cap", said: "California, Synthetic Code 1: a late fee is at most $50.00." }],
  note: "Each rule is cited information.",
};

describe("RulesBeside", () => {
  it("shows each rule cited or why it is unknown, and what goes past one as a warning", () => {
    render(<RulesBeside checked={CHECKED} label="Rules for late fees" />);
    const beside = screen.getByRole("region", { name: "Rules for late fees" });
    expect(within(beside).getByText(/Unknown until the year built is on file/)).toBeTruthy();
    const link = within(beside).getByRole("link", { name: "California: Synthetic Code 1, read 10/23/2026 by Synthetic Reviewer" });
    expect(link.getAttribute("href")).toBe("https://example.org/1");
    expect(within(beside).getByText(/Confirmed by Staff 1\./)).toBeTruthy();
    const warned = within(beside).getByRole("status");
    expect(warned.textContent).toContain("Past a rule: a warning, not a refusal");
    expect(warned.textContent).toContain("a late fee is at most $50.00.");
    expect(within(beside).getByText("Each rule is cited information.")).toBeTruthy();
  });

  it("shows nothing before the first answer, and no warning when nothing goes past", () => {
    const { container } = render(<RulesBeside checked={null} label="Rules" />);
    expect(container.textContent).toBe("");
    render(<RulesBeside checked={{ ...CHECKED, warnings: [] }} label="Rules" />);
    expect(screen.queryByRole("status")).toBeNull();
  });
});

describe("law", () => {
  it("words a place, a citation, a confirmation and the facts", () => {
    expect(placeLine({
      state_fips: "06", state_name: "California", county_fips: "999", county_name: "Demo County", cousub_fips: null,
      cousub_name: null, place_fips: null, place_name: null, tract: null, source: "staff", recorded_by: null,
      recorded_at: "2026-10-23T00:00:00Z",
    })).toBe("outside any city, Demo County, California");
    expect(citedLine(WAIT)).toBe("");
    expect(confirmedLine({ ...CAP, confirmed_by: null })).toBe("Not confirmed by your company yet");
    expect(factLines({ year_built: null, units_in_building: 4, owner_occupied: true, owner_kind: "company", recorded_by: null, recorded_at: null }))
      .toEqual([["Year built", "Not known"], ["Units in the building", "4"], ["An owner lives there", "Yes"], ["The owner is", "A company"]]);
    expect(checkQuery({ a: " 1 ", b: "", c: null, d: undefined })).toBe("a=1");
  });

  it("asks a check a moment after what it checks stops changing, and drops what isn't a check", async () => {
    vi.useFakeTimers();
    const fetch = vi.fn(async (path: string) =>
      new Response(JSON.stringify(path.includes("bad") ? { detail: "x" } : CHECKED), { status: 200 }));
    vi.stubGlobal("fetch", fetch);
    const { result, rerender } = renderHook(({ path }: { path: string | null }) => useLawCheck(path), {
      initialProps: { path: "/api/law/late-fees?property_id=p1&grace_days=1" as string | null },
    });
    rerender({ path: "/api/law/late-fees?property_id=p1&grace_days=12" });
    await act(async () => { await vi.advanceTimersByTimeAsync(300); });
    expect(fetch.mock.calls.map(([p]) => p)).toEqual(["/api/law/late-fees?property_id=p1&grace_days=12"]);
    expect(result.current?.warnings).toHaveLength(1);
    rerender({ path: "/api/law/bad" });
    await act(async () => { await vi.advanceTimersByTimeAsync(300); });
    expect(result.current).toBeNull();
    rerender({ path: null });
    expect(result.current).toBeNull();
  });
});
