import { describe, expect, it } from "vitest";
import { KINDS, kindLabel, sizeLabel, titleOf, toBase64, typeOf } from "./documents";

describe("documents", () => {
  it("names each kind and what it is about", () => {
    expect(KINDS.map((k) => [k.kind, k.about])).toEqual([
      ["w9", "Vendor"], ["receipt", "Bill"], ["move_out_photo", "Lease"], ["owner_document", "Owner"],
    ]);
    expect(kindLabel("move_out_photo")).toBe("Move-out photo");
  });

  it("sends only PDFs, JPEGs and PNGs", () => {
    expect(typeOf({ type: "application/pdf" })).toBe("application/pdf");
    expect(typeOf({ type: "image/jpeg" })).toBe("image/jpeg");
    expect(typeOf({ type: "image/png" })).toBe("image/png");
    expect(typeOf({ type: "text/html" })).toBeNull();
    expect(typeOf({ type: "" })).toBeNull();
  });

  it("reads sizes and titles", () => {
    expect([sizeLabel(512), sizeLabel(2048), sizeLabel(3 * 1024 * 1024)]).toEqual(["512 B", "2 KB", "3.0 MB"]);
    expect(titleOf("W-9 Vendor 1.pdf")).toBe("W-9 Vendor 1");
    expect(titleOf("photo.jpeg")).toBe("photo");
    expect(titleOf("no extension")).toBe("no extension");
  });

  it("turns bytes into base64, however many", () => {
    expect(toBase64(new TextEncoder().encode("%PDF-1.4"))).toBe("JVBERi0xLjQ=");
    const many = new Uint8Array(0x8000 * 2 + 5).fill(65);
    expect(atob(toBase64(many))).toBe("A".repeat(many.length));
  });
});
