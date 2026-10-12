import { describe, expect, it } from "vitest";
import { applicantStatus, incomeMultiple, needsNotice, recommendationLabel } from "./leasing";

describe("leasing helpers", () => {
  it("tells an applicant where their application stands", () => {
    expect(applicantStatus("received")).toContain("hasn't looked at it yet");
    expect(applicantStatus("in review")).toBe("In review.");
    expect(applicantStatus("approved")).toBe("Approved.");
    expect(applicantStatus("conditional")).toBe("Approved, on conditions:");
    expect(applicantStatus("declined")).toContain("Declined");
  });

  it("names what a screening recommends", () => {
    expect(["approve", "conditional", "decline"].map((r) => recommendationLabel(r as "approve"))).toEqual([
      "Recommends approval",
      "Recommends conditions",
      "Recommends declining",
    ]);
  });

  it("asks for a notice exactly when a decision against the applicant rests on a screening", () => {
    expect(needsNotice("declined", "s1")).toBe(true);
    expect(needsNotice("conditional", "s1")).toBe(true);
    expect(needsNotice("approved", "s1")).toBe(false);
    expect(needsNotice("declined", null)).toBe(false);
  });

  it("shows income against rent, rounded down, and nothing without a rent", () => {
    expect(incomeMultiple("4500.00", "1500.00")).toBe("3.0x");
    expect(incomeMultiple("4499.99", "1500.00")).toBe("2.9x");
    expect(incomeMultiple("4500.00", "0")).toBe("");
  });
});
