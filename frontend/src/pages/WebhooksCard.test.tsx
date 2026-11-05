import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { WebhooksCard } from "./WebhooksCard";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

type Hook = {
  id: string; url: string; last4: string; made_at: string; made_by: string; revoked_at: string | null;
  last_attempt_at: string | null; last_error: string | null;
};
const DELIVERING: Hook = {
  id: "w1", url: "https://hooks.example.com/occupella", last4: "ab12", made_at: "2026-11-05T15:00:00Z",
  made_by: "Manager 1", revoked_at: null, last_attempt_at: "2026-11-05T16:00:00Z", last_error: null,
};
const FAILING: Hook = {
  ...DELIVERING, id: "w2", url: "https://hooks.example.com/failing", last4: "zz99",
  last_error: "the receiver answered 500",
};
const SECRET = "whsec_made-up-secret-for-the-test-0123456789abcdef";

// A server holding the company's webhooks; adding one adds it, taking one back marks it.
function server(hooks: Hook[] = [], refuse = "") {
  let held = [...hooks];
  const fetch = vi.fn(async (path: string, init?: RequestInit) => {
    if (init?.method !== "POST") return new Response(JSON.stringify(held), { status: 200 });
    if (refuse) return new Response(JSON.stringify({ detail: refuse }), { status: 422 });
    if (path.endsWith("/revoke")) {
      held = held.map((h) => (path.includes(`/${h.id}/`) ? { ...h, revoked_at: "2026-11-05T17:00:00Z" } : h));
      return new Response(null, { status: 204 });
    }
    const { url } = JSON.parse(String(init.body)) as { url: string };
    held = [{ ...DELIVERING, id: "w3", url, last4: SECRET.slice(-4), last_attempt_at: null }, ...held];
    return new Response(JSON.stringify({ id: "w3", url, secret: SECRET }), { status: 201 });
  });
  vi.stubGlobal("fetch", fetch);
  return fetch;
}

describe("WebhooksCard", () => {
  it("lists each webhook by its address and the secret's last four, with how its deliveries go", async () => {
    server([DELIVERING, FAILING]);
    render(<WebhooksCard />);
    const cells = async (name: string) =>
      within(await screen.findByRole("row", { name })).getAllByRole("cell").map((cell) => cell.textContent);
    expect((await cells(DELIVERING.url)).slice(0, 5)).toEqual([
      DELIVERING.url, "…ab12", "11/05/2026 by Manager 1", "Active", "Delivering",
    ]);
    expect((await cells(FAILING.url))[4]).toBe("Failingthe receiver answered 500");
  });

  it("says so when there are none", async () => {
    server();
    render(<WebhooksCard />);
    expect(await screen.findByText("No webhooks yet. Software can also read events with its key.")).toBeTruthy();
  });

  it("adds a webhook in a side panel and shows its secret once", async () => {
    const fetch = server();
    render(<WebhooksCard />);
    fireEvent.click(await screen.findByRole("button", { name: "Add webhook" }));
    const panel = screen.getByRole("dialog", { name: "Add webhook" });
    fireEvent.change(within(panel).getByLabelText("Address"), { target: { value: "https://hooks.example.com/new" } });
    fireEvent.click(within(panel).getByRole("button", { name: "Add webhook" }));
    expect((await within(panel).findByLabelText("Signing secret") as HTMLInputElement).value).toBe(SECRET);
    expect(await screen.findByText("Added the webhook to https://hooks.example.com/new.")).toBeTruthy();
    const posted = fetch.mock.calls.filter(([, init]) => init?.method === "POST");
    expect(posted.map(([path, init]) => [path, JSON.parse(String(init?.body))])).toEqual([
      ["/api/ai/webhooks", { url: "https://hooks.example.com/new" }],
    ]);
    fireEvent.click(within(panel).getByRole("button", { name: "Done" }));
    expect(screen.queryByDisplayValue(SECRET)).toBeNull();
    expect((await screen.findByRole("row", { name: "https://hooks.example.com/new" })).textContent).toContain("…cdef");
  });

  it("says why an address is refused", async () => {
    server([], "a webhook goes to a public address, never the server's own network");
    render(<WebhooksCard />);
    fireEvent.click(await screen.findByRole("button", { name: "Add webhook" }));
    const panel = screen.getByRole("dialog", { name: "Add webhook" });
    fireEvent.change(within(panel).getByLabelText("Address"), { target: { value: "https://10.0.0.5/x" } });
    fireEvent.click(within(panel).getByRole("button", { name: "Add webhook" }));
    expect(await within(panel).findByText("a webhook goes to a public address, never the server's own network")).toBeTruthy();
  });

  it("takes a webhook back once confirmed", async () => {
    server([DELIVERING]);
    render(<WebhooksCard />);
    fireEvent.click(await screen.findByRole("button", { name: `Actions for ${DELIVERING.url}` }));
    fireEvent.click(await screen.findByRole("menuitem", { name: "Take back" }));
    const question = screen.getByRole("alertdialog");
    expect(question.textContent).toContain(`Take back the webhook to ${DELIVERING.url}? No event is sent to it from now on.`);
    fireEvent.click(within(question).getByRole("button", { name: "Take back" }));
    expect(await screen.findByText(`Took back the webhook to ${DELIVERING.url}.`)).toBeTruthy();
    expect((await screen.findByRole("row", { name: DELIVERING.url })).textContent).toContain("Taken back");
  });
});
