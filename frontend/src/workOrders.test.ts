import { describe, expect, it } from "vitest";
import { daysLeftLabel, ENTRY, PRIORITY, RENEWAL, STATUS, whereLabel } from "./workOrders";

describe("work orders and renewals", () => {
  it("say where a work order is", () => {
    const property = { id: "p1", name: "Property 1" };
    expect(whereLabel({ property, unit: { id: "u1", name: "Unit 1" } })).toBe("Unit 1, Property 1");
    expect(whereLabel({ property, unit: null })).toBe("Property 1");
  });

  it("say when a lease ends", () => {
    expect([0, 1, 20].map(daysLeftLabel)).toEqual(["today", "tomorrow", "in 20 days"]);
  });

  it("give each state its word and tone", () => {
    // Buildium's word for a work order nobody has been assigned yet.
    expect(STATUS.open).toEqual({ word: "New", tone: "caution" });
    expect(STATUS.scheduled).toEqual({ word: "Scheduled", tone: "info" });
    expect(PRIORITY.urgent).toEqual({ word: "Urgent", tone: "danger" });
    expect(ENTRY["call first"]).toBe("Call the tenant before going in");
    expect(STATUS.completed.tone).toBe("positive");
    expect(RENEWAL.none).toEqual({ word: "No renewal", tone: "caution" });
    expect(RENEWAL.renewed.tone).toBe("positive");
  });
});
