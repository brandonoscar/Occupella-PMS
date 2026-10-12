import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { OwnerDocuments } from "./OwnerDocuments";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

const answer = (body: unknown, status = 200) => vi.fn(async () => new Response(JSON.stringify(body), { status }));

describe("OwnerDocuments", () => {
  it("lists the owner's documents to download", async () => {
    vi.stubGlobal("fetch", answer([
      { id: "d1", title: "Management agreement", content_type: "application/pdf", size: 4096, uploaded_at: "2026-10-01T10:00:00Z" },
    ]));
    render(<OwnerDocuments />);
    const link = await screen.findByRole("link", { name: "Management agreement" });
    expect(link.getAttribute("href")).toBe("/api/owner/documents/d1/file");
    expect(screen.getByLabelText("Your documents").textContent).toContain("(4 KB, 2026-10-01)");
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
