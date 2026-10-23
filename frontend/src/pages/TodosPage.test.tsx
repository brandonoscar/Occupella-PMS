import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { Me } from "../api";
import type { Todo } from "../todos";
import { todoLine, todoStanding } from "../todos";
import { TodosPage } from "./TodosPage";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

const ME: Me = { kind: "staff", email: "clerk@example.com", name: "Clerk 1", person_id: "s1", pmc_id: "p", pmc_name: "Demo PM" };
const OPEN: Todo = {
  id: "t1", title: "Call the roofer", notes: "About the leak.", property_id: "p1", property: "Cedar House",
  assigned_to_id: "s2", assigned_to: "Clerk 2", due_on: "2026-10-20", overdue: true, written_by: "Clerk 1",
  written_at: "2026-10-18T09:00:00Z", done_by: null, done_at: null,
};
const DONE: Todo = { ...OPEN, id: "t2", title: "Order filters", overdue: false, done_by: "Clerk 2", done_at: "2026-10-21T10:00:00Z" };
const STAFF = [{ staff_id: "s1", name: "Clerk 1", roles: [] }, { staff_id: "s2", name: "Clerk 2", roles: [] }];
const PLACES = { groups: [], properties: [{ id: "p1", name: "Cedar House" }] };

function server() {
  const fetch = vi.fn(async (path: string, init?: RequestInit) => {
    if (init?.method === "POST") {
      const body = JSON.parse(String(init.body));
      if (path === "/api/todos") return new Response(JSON.stringify({ ...OPEN, id: "t3", title: body.title, assigned_to: "Clerk 1" }), { status: 201 });
      return new Response(JSON.stringify({ ...OPEN, assigned_to: "Clerk 1", due_on: body.due_on ?? OPEN.due_on }), { status: 200 });
    }
    if (path === "/api/staff") return new Response(JSON.stringify(STAFF), { status: 200 });
    if (path === "/api/property-groups") return new Response(JSON.stringify(PLACES), { status: 200 });
    const done = path.includes("status=done");
    const items = done ? [DONE] : [OPEN];
    return new Response(JSON.stringify({ items, total: 1, page: 1, per_page: 50 }), { status: 200 });
  });
  vi.stubGlobal("fetch", fetch);
  return fetch;
}

const posted = (fetch: ReturnType<typeof server>) =>
  fetch.mock.calls.filter(([, init]) => init?.method === "POST").map(([path, init]) => [path, JSON.parse(String(init?.body))]);

describe("TodosPage", () => {
  it("lists the open to-dos with whose they are and when they are due, and marks one done", async () => {
    const fetch = server();
    render(<TodosPage me={ME} />);
    const row = await screen.findByRole("row", { name: "Call the roofer" });
    expect(within(row).getByText("About the leak.")).toBeTruthy();
    expect(within(row).getByText("Cedar House")).toBeTruthy();
    expect(within(row).getByText("Clerk 2")).toBeTruthy();
    expect(within(row).getByText("10/20/2026")).toBeTruthy();
    expect(within(row).getByText("overdue")).toBeTruthy();
    fireEvent.click(within(row).getByRole("button", { name: "Done" }));
    expect(await screen.findByText("Done: Call the roofer.")).toBeTruthy();
    expect(posted(fetch)).toEqual([["/api/todos/t1/done", {}]]);
    // Done, and only mine: each asks again.
    fireEvent.click(screen.getByRole("tab", { name: "Done" }));
    expect(await screen.findByRole("row", { name: "Order filters" })).toBeTruthy();
    expect(screen.getByText("Done by Clerk 2 on 10/21/2026")).toBeTruthy();
    fireEvent.click(screen.getByRole("checkbox", { name: "Only mine" }));
    await screen.findByRole("row", { name: "Order filters" });
    expect(fetch.mock.calls.map(([p]) => p)).toContain("/api/todos?status=done&mine=true&page=1");
  });

  it("adds a to-do for someone, about a property, and reassigns one", async () => {
    const fetch = server();
    render(<TodosPage me={ME} />);
    fireEvent.click(await screen.findByRole("button", { name: "Add to-do" }));
    let panel = screen.getByRole("dialog", { name: "Add to-do" });
    fireEvent.change(within(panel).getByLabelText("To-do"), { target: { value: "Inspect the roof" } });
    fireEvent.change(within(panel).getByLabelText("Due on"), { target: { value: "2026-10-30" } });
    await within(panel).findByRole("option", { name: "Clerk 2" });
    fireEvent.change(within(panel).getByLabelText("Assign to"), { target: { value: "s2" } });
    await within(panel).findByRole("option", { name: "Cedar House" });
    fireEvent.change(within(panel).getByLabelText("About a property (optional)"), { target: { value: "p1" } });
    fireEvent.click(within(panel).getByRole("button", { name: "Add to-do" }));
    expect(await screen.findByText("Added: Inspect the roof, for Clerk 1.")).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: "Reassign" }));
    panel = screen.getByRole("dialog", { name: "Reassign: Call the roofer" });
    fireEvent.change(within(panel).getByLabelText("Due on"), { target: { value: "2026-11-02" } });
    fireEvent.click(within(panel).getByRole("button", { name: "Save" }));
    expect(await screen.findByText("Call the roofer: Clerk 1's, due 11/02/2026.")).toBeTruthy();
    expect(posted(fetch)).toEqual([
      ["/api/todos", { title: "Inspect the roof", notes: null, assigned_to: "s2", due_on: "2026-10-30", property_id: "p1" }],
      ["/api/todos/t1/assign", { assigned_to: "s2", due_on: "2026-11-02" }],
    ]);
  });
});

describe("todos", () => {
  it("words a to-do for the Dashboard and its standing", () => {
    expect(todoLine(OPEN, "2026-10-23")).toBe("Call the roofer (Cedar House): overdue, due 10/20/2026");
    expect(todoLine({ ...OPEN, overdue: false, property: null, due_on: "2026-10-23" }, "2026-10-23")).toBe("Call the roofer: due today");
    expect(todoStanding(DONE)).toEqual({ word: "done", tone: "positive" });
    expect(todoStanding({ ...OPEN, overdue: false })).toEqual({ word: "open", tone: "neutral" });
  });
});
