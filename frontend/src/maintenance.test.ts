import { describe, expect, it } from "vitest";
import { isOpen, statusLabel, when } from "./maintenance";

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

  it("shows a time in UTC to the minute, whatever the browser's zone", () => {
    expect(when("2026-10-11T18:05:09.123456Z")).toBe("10/11/2026 18:05 UTC");
    expect(when("2026-10-11T20:05:00+02:00")).toBe("10/11/2026 18:05 UTC");
    expect(when("not a time")).toBe("not a time");
  });
});
