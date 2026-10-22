import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { OwnerDocuments } from "./OwnerDocuments";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

const answer = (body: unknown, status = 200) => vi.fn(async () => new Response(JSON.stringify(body), { status }));

describe("OwnerDocuments", () => {
  it("lists the owner's documents, their properties' files and their bills' receipts to download", async () => {
    const at = "2026-10-01T10:00:00Z";
    vi.stubGlobal("fetch", answer([
      { id: "d1", title: "Management agreement", content_type: "application/pdf", size: 4096, uploaded_at: at, about: "owner", about_name: null },
      { id: "d2", title: "Insurance binder", content_type: "application/pdf", size: 4096, uploaded_at: at, about: "property", about_name: "Cedar House" },
      { id: "d3", title: "Plumber's receipt", content_type: "image/jpeg", size: 2048, uploaded_at: at, about: "bill", about_name: "Bill PLB-1, Plumbing Co" },
    ]));
    render(<OwnerDocuments />);
    const link = await screen.findByRole("link", { name: "Management agreement" });
    expect(link.getAttribute("href")).toBe("/api/owner/documents/d1/file");
    const items = screen.getAllByRole("listitem").map((item) => item.textContent);
    expect(items).toEqual([
      "Management agreement (4 KB, 10/01/2026)",
      "Insurance binder (Cedar House, 4 KB, 10/01/2026)",
      "Plumber's receipt (Bill PLB-1, Plumbing Co, 2 KB, 10/01/2026)",
    ]);
  });

  it("says when there are none, and when they can't be read", async () => {
    vi.stubGlobal("fetch", answer([]));
    render(<OwnerDocuments />);
    expect(await screen.findByText("No documents yet.")).toBeTruthy();
    cleanup();

    vi.stubGlobal("fetch", answer({ detail: "sign in first" }, 401));
    render(<OwnerDocuments />);
    expect((await screen.findAllByRole("alert"))[0]?.textContent).toBe("sign in first");
  });
});
