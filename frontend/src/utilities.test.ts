import { describe, expect, it } from "vitest";
import type { Plan, Share } from "./utilities";
import { methodLabel, plansInForce, shareAmount, utilityLabel, weightLabel } from "./utilities";

const SHARE: Share = { lease_id: "l1", home: "Tenant 1, Property 1 Unit 1", billed: true, weight: "18000", usage: null, amount: "27.50" };

function plan(utility: Plan["utility"], starts_on: string, method: Plan["method"]): Plan {
  return { id: `${utility}-${starts_on}`, utility, starts_on, method, owner_percent: "0", recorded_by: null };
}

describe("labels", () => {
  it("name utilities and methods as people say them", () => {
    expect(utilityLabel("electric")).toBe("Electric");
    expect(methodLabel("square_feet")).toBe("By square feet");
    expect(methodLabel("none")).toBe("Not billed back");
  });
});

describe("weightLabel and shareAmount", () => {
  it("say what each share was worked out from, and what it charged", () => {
    expect(weightLabel("units", { ...SHARE, weight: "30" })).toBe("30 days");
    expect(weightLabel("square_feet", SHARE)).toBe("18,000 sq ft-days");
    expect(weightLabel("occupants", { ...SHARE, weight: "60" })).toBe("60 person-days");
    expect(weightLabel("submeter", { ...SHARE, usage: "45.5" })).toBe("45.5 on its meter");
    expect(weightLabel("submeter", SHARE)).toBe("0 on its meter");
    expect(shareAmount(SHARE)).toBe("$27.50");
    expect(shareAmount({ ...SHARE, billed: false, amount: "0.00" })).toBe("Not billed");
  });
});

describe("plansInForce", () => {
  it("keeps each utility's latest plan from today or before, and drops one stopped", () => {
    const plans = [
      plan("water", "2026-01-01", "units"),
      plan("water", "2026-06-01", "occupants"),
      plan("water", "2026-12-01", "submeter"),
      plan("trash", "2026-01-01", "units"),
      plan("trash", "2026-05-01", "none"),
      plan("gas", "2026-03-01", "square_feet"),
    ];
    expect(plansInForce(plans, "2026-10-20").map((p) => [p.utility, p.method])).toEqual([
      ["water", "occupants"],
      ["gas", "square_feet"],
    ]);
  });
});
