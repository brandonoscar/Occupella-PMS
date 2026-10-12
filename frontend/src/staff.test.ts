import { describe, expect, it } from "vitest";
import type { Me } from "./api";
import { ROLES, changeLabel, holds } from "./staff";

const ME: Me = { kind: "staff", email: "s@example.com", name: "Staff 1", person_id: "s1", pmc_id: "p1", pmc_name: "PMC" };

describe("staff", () => {
  it("lists the roles the API knows, in order", () => {
    expect(ROLES.map((r) => r.role)).toEqual(["admin", "approve", "disburse"]);
  });

  it("says whether a staff member holds a role", () => {
    expect(holds({ ...ME, roles: ["approve"] }, "approve")).toBe(true);
    expect(holds({ ...ME, roles: ["approve"] }, "admin")).toBe(false);
    expect(holds(ME, "disburse")).toBe(false);
  });

  it("says what a change did", () => {
    const member = { staff_id: "s2", name: "Clerk 1", roles: [] };
    expect(changeLabel(member, "disburse", true)).toBe("Clerk 1 may now: Pay out.");
    expect(changeLabel(member, "admin", false)).toBe("Clerk 1 may no longer: Admin.");
  });
});
