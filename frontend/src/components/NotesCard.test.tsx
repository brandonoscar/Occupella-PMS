import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { NotesCard } from "./NotesCard";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

const NOTES = [
  { id: "n2", body: "Gate code changed.\nNew code given to tenant.", by: "Staff 1", at: "2026-10-12T15:00:00Z" },
  { id: "n1", body: "Asked about parking.", by: null, at: "2026-10-01T15:00:00Z" },
];

function server(notes: unknown = NOTES, refuse?: string) {
  const fetch = vi.fn(async (_path: string, init?: RequestInit) => {
    if (init?.method === "POST") {
      return refuse
        ? new Response(JSON.stringify({ detail: refuse }), { status: 404 })
        : new Response(JSON.stringify({ id: "n3" }), { status: 201 });
    }
    return new Response(JSON.stringify(notes), { status: 200 });
  });
  vi.stubGlobal("fetch", fetch);
  return fetch;
}

describe("NotesCard", () => {
  it("lists a record's notes newest first with who wrote each and when", async () => {
    const fetch = server();
    render(<NotesCard about="lease" aboutId="l1" name="Unit 1, Property 1" />);
    const list = await screen.findByRole("list", { name: "Notes, Unit 1, Property 1" });
    expect(within(list).getAllByRole("listitem").map((n) => n.textContent)).toEqual([
      "Gate code changed.\nNew code given to tenant.Staff 1 · 10/12/2026",
      "Asked about parking.Someone · 10/01/2026",
    ]);
    expect(fetch.mock.calls[0]?.[0]).toBe("/api/notes?about=lease&about_id=l1");
  });

  it("adds a note and clears the box", async () => {
    const fetch = server([]);
    render(<NotesCard about="tenant" aboutId="t1" name="Tenant 1" />);
    expect(await screen.findByText("No notes yet.")).toBeTruthy();
    fireEvent.change(screen.getByLabelText("New note"), { target: { value: "Prefers email." } });
    fireEvent.click(screen.getByRole("button", { name: "Add note" }));
    expect(await screen.findByText("Note added.")).toBeTruthy();
    expect((screen.getByLabelText("New note") as HTMLTextAreaElement).value).toBe("");
    const [, init] = fetch.mock.calls.find(([, i]) => i?.method === "POST") ?? [];
    expect(JSON.parse(String(init?.body))).toEqual({ about: "tenant", about_id: "t1", body: "Prefers email." });
  });

  it("says why a note was refused, and when notes can't be read", async () => {
    server([], "no such tenant");
    render(<NotesCard about="tenant" aboutId="t9" name="Tenant 9" />);
    await screen.findByText("No notes yet.");
    fireEvent.change(screen.getByLabelText("New note"), { target: { value: "x" } });
    fireEvent.click(screen.getByRole("button", { name: "Add note" }));
    expect((await screen.findByRole("alert")).textContent).toBe("no such tenant");
    cleanup();
    vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({ detail: "sign in first" }), { status: 401 })));
    render(<NotesCard about="unit" aboutId="u1" name="Unit 1" />);
    expect((await screen.findByRole("alert")).textContent).toBe("sign in first");
  });
});
