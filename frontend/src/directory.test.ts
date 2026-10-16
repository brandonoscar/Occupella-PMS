import { describe, expect, it } from "vitest";
import type { Contact } from "./directory";
import { addressLines, contactBody, hasContact } from "./directory";

const NONE: Contact = {
  email: null, phone: null, alternate_phone: null, address_line_1: null, address_line_2: null,
  city: null, state: null, postal_code: null,
};

describe("contact details", () => {
  it("writes a mailing address on its two lines, the second street line only when there is one", () => {
    const address = { ...NONE, address_line_1: "100 Example Street", city: "Sampletown", state: "ZZ", postal_code: "00000" };
    expect(addressLines(address)).toEqual(["100 Example Street", "Sampletown, ZZ 00000"]);
    expect(addressLines({ ...address, address_line_2: "Suite 1" })).toEqual([
      "100 Example Street, Suite 1",
      "Sampletown, ZZ 00000",
    ]);
    expect(addressLines(NONE)).toEqual([]);
  });

  it("sends each field trimmed, and an empty one as nothing", () => {
    const draft = { ...Object.fromEntries(Object.keys(NONE).map((k) => [k, ""])), email: " a@example.com ", city: "  " };
    expect(contactBody(draft as Parameters<typeof contactBody>[0])).toEqual({ ...NONE, email: "a@example.com" });
  });

  it("knows whether any detail is recorded", () => {
    expect(hasContact(NONE)).toBe(false);
    expect(hasContact({ ...NONE, alternate_phone: "555-555-0100" })).toBe(true);
  });
});
