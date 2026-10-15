import { describe, expect, it } from "vitest";
import type { CustomRole } from "./customRoles";
import { roleSummary, rolesHeld } from "./customRoles";

const CHOICES = [
  { area: "rent", label: "rent" },
  { area: "bills", label: "bills" },
  { area: "journal", label: "the journal" },
];
const role = (name: string, areas: string[], staff: string[] = []): CustomRole => ({
  id: name, name, areas, staff: staff.map((s) => ({ staff_id: s, name: s })),
});

describe("custom roles", () => {
  it("says what a role may change, in the areas' order", () => {
    expect(roleSummary(role("Bookkeeping", ["journal", "bills"]), CHOICES)).toBe(
      "Bookkeeping may change: bills, the journal.",
    );
    expect(roleSummary(role("Auditor", []), CHOICES)).toBe("Auditor may change nothing: look only.");
  });

  it("names the roles a staff member holds", () => {
    const roles = [role("Bookkeeping", [], ["s1", "s2"]), role("Leasing", [], ["s2"])];
    expect(rolesHeld(roles, "s2")).toEqual(["Bookkeeping", "Leasing"]);
    expect(rolesHeld(roles, "s3")).toEqual([]);
  });
});
