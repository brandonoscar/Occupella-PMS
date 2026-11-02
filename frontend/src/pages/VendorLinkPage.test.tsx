import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { VendorView } from "../workOrders";
import { VendorLinkPage } from "./VendorLinkPage";

beforeEach(() => {
  window.history.replaceState(null, "", "/vendor#a-token-of-enough-length");
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

const LEAK = {
  id: "w1", number: 7, property: "Property 1", unit: "Unit 1", summary: "Leak", description: "Under the sink.",
  priority: "high" as const, due_on: "2026-10-21", entry: "Call Synthetic PMC first to arrange entry.",
  status: "assigned" as const, visit_on: null, visit_window: null,
};
const VIEW: VendorView = { vendor: "Vendor 1", company: "Synthetic PMC", work_orders: [LEAK] };

function server(answers: Record<string, unknown>, refuse: Record<string, string> = {}) {
  const fetch = vi.fn(async (path: string, _init?: RequestInit) => {
    if (path in refuse) return new Response(JSON.stringify({ detail: refuse[path] }), { status: 409 });
    return new Response(JSON.stringify(answers[path] ?? VIEW), { status: 200 });
  });
  vi.stubGlobal("fetch", fetch);
  return fetch;
}

const bodies = (fetch: ReturnType<typeof server>) =>
  fetch.mock.calls.map(([path, init]) => [path, JSON.parse(String(init?.body))]);
// The plain line under a button (ADR 0010, rule 1): what describes it.
const says = (button: HTMLElement) => document.getElementById(button.getAttribute("aria-describedby") ?? "")?.textContent;

describe("VendorLinkPage", () => {
  it("shows the vendor's work orders, and schedules a visit with the token", async () => {
    const scheduled = { ...VIEW, work_orders: [{ ...LEAK, status: "scheduled", visit_on: "2026-10-20", visit_window: "8-10 am" }] };
    const fetch = server({ "/api/vendor-link/work-orders/w1/steps": scheduled });
    render(<VendorLinkPage />);
    expect(await screen.findByRole("heading", { name: "Vendor 1: work orders from Synthetic PMC" })).toBeTruthy();
    const leak = screen.getByRole("region", { name: "Leak" });
    expect(leak.textContent).toContain("Unit 1, Property 1");
    expect(leak.textContent).toContain("Under the sink.");
    expect(leak.textContent).toContain("Due by 10/21/2026. Call Synthetic PMC first to arrange entry.");

    fireEvent.change(within(leak).getByLabelText("Visit on"), { target: { value: "2026-10-20" } });
    fireEvent.change(within(leak).getByLabelText("Window (optional)"), { target: { value: " 8-10 am " } });
    expect(says(within(leak).getByRole("button", { name: "Schedule the visit" })))
      .toBe("The company and any tenant who asked for the repair see the day. No email is sent.");
    expect(says(within(leak).getByRole("button", { name: "The work is done" })))
      .toBe("Tells the company it's done and closes the work order; it leaves this list and can't be reopened.");
    fireEvent.click(within(leak).getByRole("button", { name: "Schedule the visit" }));
    expect(await screen.findByText("Visit for #7 on 10/20/2026, 8-10 am.")).toBeTruthy();
    expect(screen.getByTestId("visit").textContent).toBe("Visit on 10/20/2026, 8-10 am");
    expect(says(screen.getByRole("button", { name: "Change the visit" })))
      .toBe("The company and any tenant who asked for the repair see the new day. No email is sent.");

    expect(bodies(fetch)).toEqual([
      ["/api/vendor-link/work-orders", { token: "a-token-of-enough-length" }],
      ["/api/vendor-link/work-orders/w1/steps", {
        token: "a-token-of-enough-length", step: "scheduled", scheduled_for: "2026-10-20", visit_window: "8-10 am",
      }],
    ]);
  });

  it("says the work is done, and then that nothing waits", async () => {
    const fetch = server({ "/api/vendor-link/work-orders/w1/steps": { ...VIEW, work_orders: [] } });
    render(<VendorLinkPage />);
    const leak = await screen.findByRole("region", { name: "Leak" });
    fireEvent.change(within(leak).getByLabelText("What you did (optional)"), { target: { value: " New washer " } });
    // Saying it's done asks first; Cancel sends nothing.
    const question = "Mark #7 done? It closes the work order and leaves your list; it can't be reopened.";
    fireEvent.click(within(leak).getByRole("button", { name: "The work is done" }));
    expect(screen.getByRole("alertdialog", { name: question }).textContent).toContain(question);
    fireEvent.click(within(screen.getByRole("alertdialog")).getByRole("button", { name: "Cancel" }));
    expect(screen.queryByRole("alertdialog")).toBeNull();
    expect(bodies(fetch)).toEqual([["/api/vendor-link/work-orders", { token: "a-token-of-enough-length" }]]);
    fireEvent.click(within(leak).getByRole("button", { name: "The work is done" }));
    expect(bodies(fetch)).toHaveLength(1);
    fireEvent.click(within(screen.getByRole("alertdialog", { name: question })).getByRole("button", { name: "The work is done" }));
    expect(await screen.findByText("#7 is done. Thank you.")).toBeTruthy();
    expect(screen.getByText("No work order is waiting for you.")).toBeTruthy();
    expect(bodies(fetch)[1]).toEqual([
      "/api/vendor-link/work-orders/w1/steps", { token: "a-token-of-enough-length", step: "completed", note: "New washer" },
    ]);
  });

  it("says why a step was refused, and when the link opens nothing", async () => {
    server({}, { "/api/vendor-link/work-orders/w1/steps": "no such work order of yours" });
    render(<VendorLinkPage />);
    const leak = await screen.findByRole("region", { name: "Leak" });
    fireEvent.click(within(leak).getByRole("button", { name: "The work is done" }));
    fireEvent.click(
      within(screen.getByRole("alertdialog", {
        name: "Mark #7 done? It closes the work order and leaves your list; it can't be reopened.",
      })).getByRole("button", { name: "The work is done" }),
    );
    expect((await screen.findByRole("alert")).textContent).toBe("no such work order of yours");
    cleanup();

    server({}, { "/api/vendor-link/work-orders": "this link was taken back or doesn't exist" });
    render(<VendorLinkPage />);
    expect((await screen.findByRole("alert")).textContent).toBe("this link was taken back or doesn't exist");
  });
});
