import { describe, expect, it } from "vitest";
import type { Inspection } from "./inspections";
import { inspectionReportHref, inspectionSummary, itemsToSend } from "./inspections";

describe("inspections", () => {
  it("sends the named rooms, trimmed, and leaves blank notes out", () => {
    expect(itemsToSend([
      { room: " Kitchen ", condition: "good", note: " " },
      { room: "", condition: "fair", note: "ignored" },
      { room: "Bathroom", condition: "damaged", note: " Cracked sink " },
    ])).toEqual([
      { room: "Kitchen", condition: "good" },
      { room: "Bathroom", condition: "damaged", note: "Cracked sink" },
    ]);
  });

  it("sums an inspection up and links the lease's report", () => {
    const inspection: Inspection = {
      id: "i1", kind: "move-out", inspected_on: "2026-06-30", notes: null, recorded_by: "Staff 1",
      recorded_at: "2026-06-30T12:00:00Z",
      items: [
        { room: "Kitchen", condition: "damaged", note: null },
        { room: "Bathroom", condition: "good", note: null },
      ],
    };
    expect(inspectionSummary(inspection)).toBe("Move-out, 06/30/2026: 2 rooms, 1 damaged or missing");
    const missing = { room: "Hallway", condition: "missing" as const, note: null };
    expect(inspectionSummary({ ...inspection, items: [...inspection.items, missing] })).toBe(
      "Move-out, 06/30/2026: 3 rooms, 2 damaged or missing",
    );
    expect(inspectionSummary({ ...inspection, kind: "move-in", items: [inspection.items[1]!] })).toBe(
      "Move-in, 06/30/2026: 1 room",
    );
    expect(inspectionReportHref("l1")).toBe("/api/reports/trust_report_inspection?arg=l1&format=pdf");
  });
});
