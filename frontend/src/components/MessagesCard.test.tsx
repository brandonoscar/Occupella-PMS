import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { MessagesCard } from "./MessagesCard";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

const THREAD = [
  { id: "m1", by: "person", staff: null, body: "The gate sticks.", at: "2026-11-02T15:00:00Z" },
  { id: "m2", by: "company", staff: "Staff 1", body: "On it today.", at: "2026-11-02T16:00:00Z" },
];

function server(thread: unknown = THREAD, refuse?: string) {
  const fetch = vi.fn(async (_path: string, init?: RequestInit) => {
    if (init?.method === "POST") {
      return refuse
        ? new Response(JSON.stringify({ detail: refuse }), { status: 429 })
        : new Response(JSON.stringify({ id: "m3" }), { status: 201 });
    }
    return new Response(JSON.stringify(thread), { status: 200 });
  });
  vi.stubGlobal("fetch", fetch);
  return fetch;
}

describe("MessagesCard", () => {
  it("shows a tenant their conversation, the company's answers as the company's", async () => {
    server();
    render(<MessagesCard path="/api/tenant/messages" company="Synthetic PMC" person="Tenant 1" />);
    const list = await screen.findByRole("list", { name: "Messages with Synthetic PMC" });
    const items = within(list).getAllByRole("listitem").map((li) => li.textContent ?? "");
    expect(items[0]).toMatch(/^You · 11\/02\/2026 .*The gate sticks\.$/);
    expect(items[1]).toMatch(/^Synthetic PMC · 11\/02\/2026 .*On it today\.$/);
    expect(items[1]).not.toContain("Staff 1");
  });

  it("names the staff member to staff, and the person by name", async () => {
    server({ about: "tenant", person_id: "t1", name: "Tenant 1", messages: THREAD });
    render(<MessagesCard path="/api/messages/tenant/t1" company="Synthetic PMC" person="Tenant 1" staff />);
    const list = await screen.findByRole("list", { name: "Messages with Tenant 1" });
    const items = within(list).getAllByRole("listitem").map((li) => li.textContent ?? "");
    expect(items[0]).toMatch(/^Tenant 1 · /);
    expect(items[1]).toMatch(/^Staff 1 \(Synthetic PMC\) · /);
  });

  it("sends a message, says so and reads the conversation again", async () => {
    const fetch = server();
    render(<MessagesCard path="/api/owner/messages" company="Synthetic PMC" person="Owner 1" />);
    await screen.findByRole("list");
    fireEvent.change(screen.getByLabelText("Write to Synthetic PMC"), { target: { value: "When is the roof done?" } });
    fireEvent.click(screen.getByRole("button", { name: "Send message" }));
    expect(await screen.findByText("Sent to Synthetic PMC.")).toBeTruthy();
    const post = fetch.mock.calls.find(([, init]) => init?.method === "POST");
    expect(post?.[0]).toBe("/api/owner/messages");
    expect(JSON.parse(String(post?.[1]?.body))).toEqual({ body: "When is the roof done?" });
    expect(fetch.mock.calls.filter(([, init]) => !init?.method || init.method === "GET").length).toBe(2);
  });

  it("says when there is nothing yet, and shows a refusal in words", async () => {
    server([], "at most 20 messages a day; call the office if it can't wait");
    render(<MessagesCard path="/api/tenant/messages" company="Synthetic PMC" person="Tenant 1" />);
    expect(await screen.findByText("No messages yet.")).toBeTruthy();
    fireEvent.change(screen.getByLabelText("Write to Synthetic PMC"), { target: { value: "hi" } });
    fireEvent.click(screen.getByRole("button", { name: "Send message" }));
    expect(await screen.findByText(/at most 20 messages a day/)).toBeTruthy();
  });
});
