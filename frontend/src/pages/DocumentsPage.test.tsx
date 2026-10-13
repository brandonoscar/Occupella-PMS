import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { Me } from "../api";
import type { Library } from "../documents";
import { DocumentsPage } from "./DocumentsPage";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

const ME: Me = { kind: "staff", email: "staff@example.com", name: "Staff 1", person_id: "s1", pmc_id: "p1", pmc_name: "Synthetic PMC" };

const LIBRARY: Library = {
  documents: [
    {
      id: "d1", kind: "w9", about_id: "v1", about: "Vendor 1", title: "W-9 2026", content_type: "application/pdf",
      size: 2048, uploaded_by: "Staff 1", uploaded_at: "2026-10-01T10:00:00Z",
    },
  ],
  subjects: {
    w9: [{ id: "v1", label: "Vendor 1" }],
    receipt: [],
    move_out_photo: [{ id: "l1", label: "Property 1 / Unit 1: Tenant 1" }],
    owner_document: [{ id: "o1", label: "Owner 1" }, { id: "o2", label: "Owner 2" }],
  },
};

function server(refuse: Record<string, string> = {}, library: Library = LIBRARY) {
  const fetch = vi.fn(async (path: string, init?: RequestInit) => {
    if (init?.method !== "POST") return new Response(JSON.stringify(library), { status: 200 });
    if (path in refuse) return new Response(JSON.stringify({ detail: refuse[path] }), { status: 409 });
    if (path.endsWith("/remove")) return new Response(null, { status: 204 });
    return new Response(JSON.stringify({ id: "d2" }), { status: 201 });
  });
  vi.stubGlobal("fetch", fetch);
  return fetch;
}

const posted = (fetch: ReturnType<typeof server>) =>
  fetch.mock.calls
    .filter(([, init]) => init?.method === "POST")
    .map(([path, init]) => [path, JSON.parse(String(init?.body))]);

const pdf = (name: string, size?: number) => {
  const file = new File(["%PDF-1.4"], name, { type: "application/pdf" });
  if (size !== undefined) Object.defineProperty(file, "size", { value: size });
  return file;
};

describe("DocumentsPage", () => {
  it("keeps a file as a document about what staff choose, titled after it", async () => {
    const fetch = server();
    render(<DocumentsPage me={ME} />);
    fireEvent.change(await screen.findByLabelText("Kind"), { target: { value: "owner_document" } });
    fireEvent.change(screen.getByLabelText("Owner"), { target: { value: "o2" } });
    fireEvent.change(screen.getByLabelText("File"), { target: { files: [pdf("Owner 2 agreement.pdf")] } });
    await vi.waitFor(() => expect((screen.getByLabelText("Title") as HTMLInputElement).value).toBe("Owner 2 agreement"));
    fireEvent.click(screen.getByRole("button", { name: "Keep it" }));

    expect(await screen.findByText("Kept Owner 2 agreement.")).toBeTruthy();
    expect(posted(fetch)).toEqual([
      ["/api/documents", {
        kind: "owner_document", about_id: "o2", title: "Owner 2 agreement",
        content_type: "application/pdf", content_base64: "JVBERi0xLjQ=",
      }],
    ]);
  });

  it("lists documents to download and removes one", async () => {
    const fetch = server();
    render(<DocumentsPage me={ME} />);
    const listed = await screen.findByLabelText("Documents");
    expect(listed.textContent).toContain("W-9, Vendor 1, 2 KB, by Staff 1 on 2026-10-01");
    expect(screen.getByRole("link", { name: "W-9 2026" }).getAttribute("href")).toBe("/api/documents/d1/file");
    fireEvent.click(screen.getByRole("button", { name: "Remove W-9 2026" }));
    expect(await screen.findByText("Removed W-9 2026.")).toBeTruthy();
    expect(posted(fetch)).toEqual([["/api/documents/d1/remove", {}]]);
  });

  it("refuses a file a document can't be, says when there is nothing to keep one for, and shows a refusal", async () => {
    server({ "/api/documents": "nothing of this PMC for the document to be about" });
    render(<DocumentsPage me={ME} />);
    const keep = (await screen.findByRole("button", { name: "Keep it" })) as HTMLButtonElement;
    fireEvent.change(screen.getByLabelText("File"), { target: { files: [new File(["<html>"], "page.html", { type: "text/html" })] } });
    expect(await screen.findByText("A document is a PDF, a JPEG or a PNG.")).toBeTruthy();
    expect(keep.disabled).toBe(true);
    fireEvent.change(screen.getByLabelText("File"), { target: { files: [pdf("big.pdf", 6 * 1024 * 1024)] } });
    expect(await screen.findByText("A document is at most 5 MB.")).toBeTruthy();
    fireEvent.change(screen.getByLabelText("File"), { target: { files: [pdf("W-9.pdf")] } });
    await vi.waitFor(() => expect(keep.disabled).toBe(false));
    fireEvent.click(keep);
    expect(await screen.findByText("nothing of this PMC for the document to be about")).toBeTruthy();
    fireEvent.change(screen.getByLabelText("Kind"), { target: { value: "receipt" } });
    expect(screen.getByText("Nothing to keep a Receipt for yet.")).toBeTruthy();
    fireEvent.change(screen.getByLabelText("File"), { target: { files: [] } });
    await vi.waitFor(() => expect(keep.disabled).toBe(true));
    cleanup();

    server({}, { ...LIBRARY, documents: [] });
    render(<DocumentsPage me={ME} />);
    expect(await screen.findByText("No document yet.")).toBeTruthy();
    cleanup();

    vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({ detail: "sign in first" }), { status: 401 })));
    render(<DocumentsPage me={ME} />);
    expect((await screen.findAllByRole("alert"))[0]?.textContent).toBe("sign in first");
  });
});
