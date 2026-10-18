import { describe, expect, it } from "vitest";
import type { RecurringWorkOrder } from "./workOrders";
import { daysLabel, daysLeftLabel, ENTRY, PRIORITY, RENEWAL, STATUS, recurringLine, whereLabel } from "./workOrders";

describe("days open and recurring work orders", () => {
  it("counts days, and reads a recurring work order in a line", () => {
    expect([0, 1, 12].map(daysLabel)).toEqual(["0 days", "1 day", "12 days"]);
    const filter: RecurringWorkOrder = {
      id: "r1", property_id: "p1", property: "Property 1", unit_id: "u1", unit: "Unit 1", summary: "Filters",
      every_months: 3, schedule: "every 3 months on the 15th", day_of_month: 15, starts_on: "2026-01-20",
      priority: "normal", vendor: null, ends_on: null, last_opened_on: null,
    };
    expect(recurringLine(filter)).toBe("Filters, Unit 1, Property 1: every 3 months on the 15th from 01/20/2026");
    expect(recurringLine({ ...filter, unit: null, unit_id: null })).toBe(
      "Filters, Property 1: every 3 months on the 15th from 01/20/2026",
    );
  });
});

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
