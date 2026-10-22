import { cleanup, render, screen, within } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import type { Me } from "../api";
import type { Table } from "../stateRules";
import { StateRulesPage } from "./StateRulesPage";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

const ME: Me = {
  kind: "staff",
  email: "staff@example.com",
  name: "Staff 1",
  person_id: "s1",
  pmc_id: "p1",
  pmc_name: "Synthetic PMC",
};

const TABLE: Table = {
  checked_on: "2026-10-22",
  note: "No attorney has reviewed these state rules.",
  states: [
    {
      state: "CA",
      name: "California",
      late_fee: null,
      rent_increase: { words: "30 days; 90 for more than 10%", cite: "Cal. Civ. Code 827", url: "https://example.org/827", how: "excerpt" },
      entry: { words: "24 hours' written notice", cite: "Cal. Civ. Code 1954", url: "https://example.org/1954", how: "secondary" },
    },
  ],
};

it("says first that no attorney reviewed it, then each state's rules with their statutes", async () => {
  vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify(TABLE), { status: 200 })));

  render(<StateRulesPage me={ME} />);

  expect((await screen.findByTestId("not-reviewed")).textContent).toBe("No attorney has reviewed these state rules.");
  expect(screen.getByText(/Researched 10\/22\/2026/)).toBeTruthy();
  const row = screen.getByRole("row", { name: "California" });
  const cells = within(row).getAllByRole("cell").map((cell) => cell.textContent);
  expect(cells).toEqual([
    "California (CA)",
    "No number found",
    "30 days; 90 for more than 10%. Cal. Civ. Code 827, the statute's words, quoted in a search excerpt",
    "24 hours' written notice. Cal. Civ. Code 1954, a summary only Not verified",
  ]);
  expect(within(row).getByRole("link", { name: "Cal. Civ. Code 827" }).getAttribute("href")).toBe("https://example.org/827");
});
