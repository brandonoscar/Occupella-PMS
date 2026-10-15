import { describe, expect, it } from "vitest";
import { standingTone, vendorInsuranceHref } from "./vendors";

describe("vendors", () => {
  it("shows how coverage stands as a badge tone", () => {
    expect(standingTone("current")).toBe("positive");
    expect(standingTone("expires in 15 days")).toBe("caution");
    expect(standingTone("expires today")).toBe("caution");
    expect(standingTone("expired")).toBe("danger");
  });

  it("links the vendor insurance report as of a day", () => {
    expect(vendorInsuranceHref("2026-06-30")).toBe(
      "/api/reports/trust_report_vendor_insurance?arg=2026-06-30&format=pdf",
    );
  });
});
