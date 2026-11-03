import { describe, expect, it } from "vitest";
import { fileText } from "./fileText";

// "José O’Brien" as Excel on Windows saves it (Windows-1252), and as UTF-8.
const WINDOWS_1252 = new Uint8Array([0x4a, 0x6f, 0x73, 0xe9, 0x20, 0x4f, 0x92, 0x42, 0x72, 0x69, 0x65, 0x6e]);
const blob = (part: BlobPart) => new Blob([part]);

describe("fileText", () => {
  it("reads a UTF-8 file as written, its byte order mark dropped", async () => {
    expect(await fileText(blob("Tenant\nJosé O’Brien\nNguyễn Văn An\n"))).toBe("Tenant\nJosé O’Brien\nNguyễn Văn An\n");
    const marked = new Uint8Array([0xef, 0xbb, 0xbf, ...new TextEncoder().encode("Tenant\nZoë\n")]);
    expect(await fileText(blob(marked))).toBe("Tenant\nZoë\n");
  });

  it("reads a file Excel on Windows saved as CSV (Windows-1252) without losing its letters", async () => {
    const text = await fileText(blob(WINDOWS_1252));
    expect(text).toBe("José O’Brien");
    expect(text).not.toContain("�");
  });

  it("reads every Windows-1252 byte as the Encoding Standard maps it", async () => {
    const bytes = new Uint8Array([0x80, 0x85, 0x91, 0x93, 0x94, 0x96, 0x97, 0x99, 0x9f, 0xa0, 0xe9, 0xff]);
    expect(await fileText(blob(bytes))).toBe("€…‘“”–—™Ÿ\u00a0éÿ");
  });

  it("reads an empty file as nothing", async () => {
    expect(await fileText(blob(""))).toBe("");
  });
});
