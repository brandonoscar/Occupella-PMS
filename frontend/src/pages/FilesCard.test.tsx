import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { Library } from "../documents";
import { FilesCard } from "./FilesCard";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

const LIBRARY: Library = {
  documents: [
    { id: "d1", kind: "owner_document", about_id: "o1", about: "Owner 1", title: "Agreement", content_type: "application/pdf",
      size: 2048, uploaded_by: "Staff 1", uploaded_at: "2026-10-02T15:00:00Z" },
    // Another owner's, and a vendor's W-9: neither is this owner's file.
    { id: "d2", kind: "owner_document", about_id: "o2", about: "Owner 2", title: "Other agreement", content_type: "application/pdf",
      size: 10, uploaded_by: null, uploaded_at: "2026-10-01T15:00:00Z" },
    { id: "d3", kind: "w9", about_id: "o1", about: "Vendor 1", title: "W-9", content_type: "image/png",
      size: 10, uploaded_by: null, uploaded_at: "2026-10-01T15:00:00Z" },
  ],
  subjects: {
    w9: [], receipt: [], move_out_photo: [], owner_document: [], work_order_photo: [], property_document: [],
    unit_document: [], tenant_document: [], lease_document: [], vendor_document: [], request_photo: [],
  },
};

function server(library: unknown = LIBRARY, status = 200) {
  const fetch = vi.fn(async (_path: string, init?: RequestInit) =>
    init?.method === "POST"
      ? new Response(JSON.stringify({ id: "d9" }), { status: 201 })
      : new Response(JSON.stringify(library), { status }),
  );
  vi.stubGlobal("fetch", fetch);
  return fetch;
}

const posted = (fetch: ReturnType<typeof server>) =>
  fetch.mock.calls
    .filter(([, init]) => init?.method === "POST")
    .map(([path, init]) => [path, JSON.parse(String(init?.body))]);

describe("FilesCard", () => {
  it("lists this record's files only, each to download or remove", async () => {
    const fetch = server();
    render(<FilesCard kind="owner_document" aboutId="o1" name="Owner 1" />);

    const files = await screen.findByRole("list", { name: "Files, Owner 1" });
    expect(within(files).getAllByRole("listitem")).toHaveLength(1);
    expect(within(files).getByRole("link", { name: "Agreement" }).getAttribute("href")).toBe("/api/documents/d1/file");
    expect(files.textContent).toContain("Owner document, 2 KB, uploaded 10/02/2026");

    fireEvent.click(screen.getByRole("button", { name: "Remove Agreement" }));
    expect(await screen.findByText("Removed Agreement.")).toBeTruthy();
    expect(posted(fetch)).toEqual([["/api/documents/d1/remove", {}]]);
  });

  it("uploads a file about this record, and refuses one that isn't a PDF, JPEG or PNG", async () => {
    const fetch = server({ ...LIBRARY, documents: [] });
    render(<FilesCard kind="w9" aboutId="v1" name="Vendor 1" />);
    expect(await screen.findByText("No file yet.")).toBeTruthy();

    const input = screen.getByLabelText("W-9 file");
    fireEvent.change(input, { target: { files: [new File(["GIF89a"], "w9.gif", { type: "image/gif" })] } });
    expect(await screen.findByText("A file is a PDF, a JPEG or a PNG.")).toBeTruthy();
    expect((screen.getByRole("button", { name: "Upload" }) as HTMLButtonElement).disabled).toBe(true);

    // The title comes from the first file picked, and stays what staff typed.
    expect((screen.getByLabelText("Title") as HTMLInputElement).value).toBe("w9");
    fireEvent.change(screen.getByLabelText("Title"), { target: { value: "W-9 2026" } });
    fireEvent.change(input, { target: { files: [new File(["%PDF-1.4"], "w9-2026.pdf", { type: "application/pdf" })] } });
    await vi.waitFor(() => expect((screen.getByRole("button", { name: "Upload" }) as HTMLButtonElement).disabled).toBe(false));
    fireEvent.click(screen.getByRole("button", { name: "Upload" }));
    expect(await screen.findByText("Uploaded W-9 2026.")).toBeTruthy();
    expect(posted(fetch)).toEqual([
      ["/api/documents", { kind: "w9", about_id: "v1", title: "W-9 2026", content_type: "application/pdf", content_base64: "JVBERi0xLjQ=" }],
    ]);
    expect((screen.getByLabelText("Title") as HTMLInputElement).value).toBe("");
  });

  it("lists and takes each category the record takes, a lease's documents beside its move-out photos", async () => {
    const fetch = server({
      ...LIBRARY,
      documents: [
        { ...LIBRARY.documents[0]!, id: "p1", kind: "move_out_photo", about_id: "l1", title: "Kitchen" },
        { ...LIBRARY.documents[0]!, id: "p2", kind: "lease_document", about_id: "l1", title: "Signed lease" },
      ],
    });
    render(<FilesCard kind="move_out_photo" also={["lease_document"]} aboutId="l1" name="Unit 1, Property 1" />);

    const list = await screen.findByRole("list", { name: "Files, Unit 1, Property 1" });
    expect(within(list).getAllByRole("listitem").map((i) => i.textContent?.split("Move-out")[0]?.split("Lease")[0])).toEqual([
      "Kitchen", "Signed lease",
    ]);
    fireEvent.change(screen.getByLabelText("Category"), { target: { value: "lease_document" } });
    const file = new File(["%PDF-1.4"], "renewal.pdf", { type: "application/pdf" });
    fireEvent.change(screen.getByLabelText("Lease document file"), { target: { files: [file] } });
    await vi.waitFor(() => expect((screen.getByLabelText("Title") as HTMLInputElement).value).toBe("renewal"));
    fireEvent.click(screen.getByRole("button", { name: "Upload" }));
    await vi.waitFor(() => expect(posted(fetch)).toHaveLength(1));
    expect(posted(fetch)[0]?.[1]).toMatchObject({ kind: "lease_document", about_id: "l1", title: "renewal" });
  });

  it("says when the files can't be read", async () => {
    server({ detail: "sign in first" }, 401);
    render(<FilesCard kind="move_out_photo" aboutId="l1" name="Unit 1, Property 1" />);
    expect((await screen.findByRole("alert")).textContent).toBe("sign in first");
    expect(screen.queryByRole("button", { name: "Upload" })).toBeNull();
  });
});
