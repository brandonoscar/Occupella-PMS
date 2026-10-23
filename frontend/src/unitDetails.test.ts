import { describe, expect, it } from "vitest";
import { detailsBody, detailsForm, sizeLine } from "./unitDetails";

const NONE = { bedrooms: null, bathrooms: null, square_feet: null, market_rent: null };

describe("unit details", () => {
  it("reads a unit's size with what is recorded", () => {
    expect(sizeLine({ bedrooms: 2, bathrooms: "1.5", square_feet: 1250 })).toBe("2 beds, 1.5 baths, 1,250 sq ft");
    expect(sizeLine({ bedrooms: 0, bathrooms: "1", square_feet: null })).toBe("Studio, 1 bath");
    expect(sizeLine({ bedrooms: 1, bathrooms: null, square_feet: 500 })).toBe("1 bed, 500 sq ft");
    expect(sizeLine(NONE)).toBe("");
  });

  it("fills a form from the details and sends what was typed, empty as not recorded", () => {
    const form = detailsForm({ bedrooms: 2, bathrooms: "1.5", square_feet: 850, market_rent: "1250.00" });
    expect(form).toEqual({ bedrooms: "2", bathrooms: "1.5", square_feet: "850", market_rent: "1250.00" });
    expect(detailsForm(NONE)).toEqual({ bedrooms: "", bathrooms: "", square_feet: "", market_rent: "" });
    expect(detailsBody({ bedrooms: " 3 ", bathrooms: "", square_feet: "1,100", market_rent: "$ 1,325.50" })).toEqual({
      bedrooms: "3", bathrooms: null, square_feet: "1100", market_rent: "1325.50",
    });
    expect(detailsBody(detailsForm(NONE))).toEqual(NONE);
    expect(detailsBody({ ...detailsForm(NONE), market_rent: "$$1,325" }).market_rent).toBe("1325");
  });
});
