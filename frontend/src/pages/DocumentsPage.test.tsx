import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { Me } from "../api";
import type { FileRow, Library } from "../documents";
import type { Paged } from "../rentals";
import { DocumentsPage } from "./DocumentsPage";

beforeEach(() => {
  window.localStorage.clear();
  window.history.replaceState(null, "", "/files");
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

const ME: Me = {
  kind: "staff", email: "staff@example.com", name: "Staff 1", person_id: "s1", pmc_id: "p1",
  pmc_name: "Synthetic PMC",
};

const ROW: FileRow = {
  id: "d1", kind: "property_document", about_id: "p1", about: "Property 1", about_path: "/properties/p1",
  title: "Deed", content_type: "application/pdf", size: 2048, uploaded_by: "Staff 1", uploaded_at: "2026-10-02T15:00:00Z",
};
const PAGE: Paged<FileRow> = { items: [ROW], total: 1, page: 1, per_page: 50 };
const NONE = {
  w9: [], receipt: [], move_out_photo: [], owner_document: [], work_order_photo: [], property_document: [],
  unit_document: [], tenant_document: [], lease_document: [], vendor_document: [], request_photo: [], listing_photo: [],
};
const LIBRARY: Library = {
  documents: [],
  subjects: {
    ...NONE,
    property_document: [{ id: "p1", label: "Property 1" }, { id: "p2", label: "Property 2" }],
    vendor_document: [{ id: "v1", label: "Vendor 1" }],
    w9: [{ id: "v1", label: "Vendor 1" }],
  },
};

function server(answers: Record<string, unknown> = {}, refuse = "") {
  const fetch = vi.fn(async (path: string, init?: RequestInit) => {
    if (init?.method === "POST") {
      if (refuse) return new Response(JSON.stringify({ detail: refuse }), { status: 422 });
      if (path.endsWith("/remove")) return new Response(null, { status: 204 });
      return new Response(JSON.stringify({ id: "d9" }), { status: 201 });
    }
    const body =
      path in answers ? answers[path]
      : path.startsWith("/api/documents/files") ? PAGE
      : path === "/api/documents" ? LIBRARY
      : path === "/api/property-groups" ? { groups: [{ id: "g1", name: "North", properties: [] }], properties: [] }
      : { detail: "not found" };
    return new Response(JSON.stringify(body), { status: "detail" in (body as object) ? 401 : 200 });
  });
  vi.stubGlobal("fetch", fetch);
  return fetch;
}

const asked = (fetch: ReturnType<typeof server>) => fetch.mock.calls.filter(([, i]) => i?.method !== "POST").map(([p]) => p);
const posted = (fetch: ReturnType<typeof server>) =>
  fetch.mock.calls.filter(([, init]) => init?.method === "POST").map(([path, init]) => [path, init?.body ? JSON.parse(String(init.body)) : null]);

describe("DocumentsPage", () => {
  it("lists every file with what it is attached to, its category, day and who uploaded it", async () => {
    server();
    render(<DocumentsPage me={ME} />);

    const row = await screen.findByRole("row", { name: "Deed" });
    expect(within(row).getAllByRole("cell").map((c) => c.textContent).slice(0, 5)).toEqual([
      "Deed2 KB", "Property 1", "Property document", "10/02/2026", "Staff 1",
    ]);
    expect(within(row).getByRole("link", { name: "Deed" }).getAttribute("href")).toBe("/api/documents/d1/file");
    expect(within(row).getByRole("link", { name: "Property 1" }).getAttribute("href")).toBe("/properties/p1");
  });

  it("narrows by category and property group, from the first page", async () => {
    const fetch = server();
    render(<DocumentsPage me={ME} />);
    await screen.findByRole("row", { name: "Deed" });

    fireEvent.change(screen.getByLabelText("Category"), { target: { value: "w9" } });
    await vi.waitFor(() => expect(asked(fetch)).toContain("/api/documents/files?page=1&kind=w9"));
    await screen.findByRole("option", { name: "North" });
    fireEvent.change(screen.getByLabelText("Properties shown"), { target: { value: "group:g1" } });
    await vi.waitFor(() => expect(asked(fetch)).toContain("/api/documents/files?page=1&group=g1&kind=w9"));
  });

  it("removes a file from its row's menu, once confirmed", async () => {
    const fetch = server();
    render(<DocumentsPage me={ME} />);
    await screen.findByRole("row", { name: "Deed" });

    fireEvent.click(screen.getByRole("button", { name: "Actions for Deed" }));
    fireEvent.click(screen.getByRole("menuitem", { name: "Remove" }));
    fireEvent.click(within(screen.getByRole("alertdialog")).getByRole("button", { name: "Remove" }));
    expect(await screen.findByText("Removed Deed.")).toBeTruthy();
    expect(posted(fetch)).toEqual([["/api/documents/d1/remove", {}]]);
  });

  it("uploads a file attached to a record, in its category, from a side panel", async () => {
    const fetch = server();
    render(<DocumentsPage me={ME} />);
    await screen.findByRole("row", { name: "Deed" });

    fireEvent.click(screen.getByRole("button", { name: "Upload" }));
    const panel = screen.getByRole("dialog", { name: "Upload" });
    const file = new File(["%PDF-1.4"], "contract.pdf", { type: "application/pdf" });
    fireEvent.change(await within(panel).findByLabelText("File"), { target: { files: [file] } });
    await vi.waitFor(() => expect((within(panel).getByLabelText("Name") as HTMLInputElement).value).toBe("contract"));
    fireEvent.change(within(panel).getByLabelText("Attached to"), { target: { value: "Vendor" } });
    // A vendor takes their documents and their W-9s.
    expect(within(within(panel).getByLabelText("Category")).getAllByRole("option").map((o) => o.textContent)).toEqual([
      "Vendor document", "W-9",
    ]);
    expect((within(panel).getByLabelText("Vendor") as HTMLSelectElement).value).toBe("v1");
    fireEvent.click(within(panel).getByRole("button", { name: "Upload" }));
    expect(await screen.findByText("Uploaded contract.")).toBeTruthy();
    expect(screen.queryByRole("dialog")).toBeNull();

    expect(posted(fetch)[0]?.[1]).toMatchObject({ kind: "vendor_document", about_id: "v1", title: "contract" });
  });

  it("says when there is nothing to attach a file to, why one was refused, and when none can be read", async () => {
    server({ "/api/documents": { documents: [], subjects: NONE } }, "the file isn't a PDF");
    render(<DocumentsPage me={ME} />);
    await screen.findByRole("row", { name: "Deed" });
    fireEvent.click(screen.getByRole("button", { name: "Upload" }));
    const panel = screen.getByRole("dialog", { name: "Upload" });
    expect(await within(panel).findByText("No property to attach a file to yet.")).toBeTruthy();
    cleanup();

    vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({ detail: "sign in first" }), { status: 401 })));
    render(<DocumentsPage me={ME} />);
    expect((await screen.findAllByRole("alert"))[0]?.textContent).toBe("sign in first");
  });
});
