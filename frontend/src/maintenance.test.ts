import { describe, expect, it } from "vitest";
import { PHOTOS_PER_REQUEST, isOpen, photosLabel, photosProblem, statusLabel, visitLabel, when } from "./maintenance";

describe("maintenance status", () => {
  it("reads every status in words, and only completed and cancelled are closed", () => {
    const all = ["received", "opened", "assigned", "completed", "cancelled"] as const;

    expect(all.map(statusLabel)).toEqual([
      "Received",
      "Work order opened",
      "Someone is assigned",
      "Done",
      "Closed without work",
    ]);
    expect(all.filter(isOpen)).toEqual(["received", "opened", "assigned"]);
  });

  it("reads a scheduled visit's day and window, and only while it is scheduled", () => {
    expect(visitLabel({ status: "scheduled", visit_on: "2026-11-03", visit_window: "8-10 am" })).toBe(
      "Visit on 11/03/2026, 8-10 am",
    );
    expect(visitLabel({ status: "scheduled", visit_on: "2026-11-03", visit_window: null })).toBe("Visit on 11/03/2026");
    expect(visitLabel({ status: "completed", visit_on: "2026-11-03", visit_window: "8-10 am" })).toBeNull();
    expect(visitLabel({ status: "scheduled", visit_on: null, visit_window: null })).toBeNull();
  });

  it("counts photos, and says why the ones chosen can't be sent", () => {
    expect([0, 1, 3].map(photosLabel)).toEqual([null, "1 photo", "3 photos"]);
    const png = { type: "image/png", size: 10 };
    expect(photosProblem([png, png, png], 100)).toBe("");
    expect(PHOTOS_PER_REQUEST).toBe(3);
    expect(photosProblem([png, png, png, png], 100)).toBe("At most 3 photos a request.");
    expect(photosProblem([png, { type: null, size: 10 }], 100)).toBe("A photo is a JPEG, a PNG or a PDF.");
    expect(photosProblem([{ type: "image/png", size: 101 }], 100)).toBe("A photo is at most 5 MB.");
  });

  it("shows a time in UTC to the minute, whatever the browser's zone", () => {
    expect(when("2026-10-11T18:05:09.123456Z")).toBe("10/11/2026 18:05 UTC");
    expect(when("2026-10-11T20:05:00+02:00")).toBe("10/11/2026 18:05 UTC");
    expect(when("not a time")).toBe("not a time");
  });
});
