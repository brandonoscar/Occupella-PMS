import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { Me } from "../api";
import { AiKeysPage } from "./AiKeysPage";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

const ADMIN: Me = {
  kind: "staff", email: "manager@example.com", name: "Manager 1", person_id: "s1", pmc_id: "p1",
  pmc_name: "Synthetic PMC", roles: ["admin", "approve", "disburse"],
};
const CLERK: Me = { ...ADMIN, name: "Clerk 1", person_id: "s2", roles: ["approve"] };

type Key = { id: string; name: string; prefix: string; made_at: string; made_by: string; revoked_at: string | null };

const STANDING: Key = {
  id: "k1", name: "Reporting script", prefix: "occ_ab12cd34", made_at: "2026-10-20T15:00:00Z",
  made_by: "Manager 1", revoked_at: null,
};
const TAKEN_BACK: Key = {
  id: "k0", name: "Old assistant", prefix: "occ_zz99yy88", made_at: "2026-09-01T09:00:00Z",
  made_by: "Manager 1", revoked_at: "2026-10-01T09:00:00Z",
};
const SECRET = "occ_made-up-secret-for-the-test-0123456789abcdefgh";

// A server holding the company's keys; making one adds it, taking one back marks it.
function server(keys: Key[] = [], refuse = "") {
  let held = [...keys];
  const fetch = vi.fn(async (path: string, init?: RequestInit) => {
    if (init?.method !== "POST") return new Response(JSON.stringify(held), { status: 200 });
    if (refuse) return new Response(JSON.stringify({ detail: refuse }), { status: 409 });
    if (path.endsWith("/revoke")) {
      held = held.map((k) => (path.includes(`/${k.id}/`) ? { ...k, revoked_at: "2026-10-31T12:00:00Z" } : k));
      return new Response(null, { status: 204 });
    }
    const { name } = JSON.parse(String(init.body)) as { name: string };
    const made: Key = { id: "k2", name, prefix: SECRET.slice(0, 12), made_at: "2026-10-31T12:00:00Z", made_by: "Manager 1", revoked_at: null };
    held = [made, ...held];
    return new Response(JSON.stringify({ id: "k2", name, prefix: made.prefix, secret: SECRET }), { status: 201 });
  });
  vi.stubGlobal("fetch", fetch);
  return fetch;
}

const posted = (fetch: ReturnType<typeof server>) =>
  fetch.mock.calls.filter(([, init]) => init?.method === "POST").map(([path, init]) => [path, JSON.parse(String(init?.body))]);

describe("AiKeysPage", () => {
  it("says so when the company has no key", async () => {
    server();
    render(<AiKeysPage me={ADMIN} />);
    expect((await screen.findByTestId("empty-state")).textContent).toContain("No API keys yet.");
  });

  it("lists each key by its first characters, standing or taken back, never its secret", async () => {
    server([STANDING, TAKEN_BACK]);
    render(<AiKeysPage me={ADMIN} />);

    const cells = async (name: string) =>
      within(await screen.findByRole("row", { name })).getAllByRole("cell").map((cell) => cell.textContent);
    expect((await cells("Reporting script")).slice(0, 5)).toEqual(["Reporting script", "occ_ab12cd34…", "10/20/2026", "Manager 1", "Active"]);
    expect((await cells("Old assistant")).slice(0, 5)).toEqual(["Old assistant", "occ_zz99yy88…", "09/01/2026", "Manager 1", "Taken back10/01/2026"]);
    // Only a standing key has a menu to take it back.
    expect(within(screen.getByRole("row", { name: "Old assistant" })).queryByRole("button")).toBeNull();
    expect(screen.queryByLabelText("Secret key")).toBeNull();
  });

  it("makes a key in a side panel and shows its secret once", async () => {
    const fetch = server();
    render(<AiKeysPage me={ADMIN} />);
    await screen.findByTestId("empty-state");

    fireEvent.click(screen.getByRole("button", { name: "Make a key" }));
    const panel = screen.getByRole("dialog", { name: "Make a key" });
    fireEvent.change(within(panel).getByLabelText("Name"), { target: { value: "Occupella AI" } });
    fireEvent.click(within(panel).getByRole("button", { name: "Make key" }));

    expect((await within(panel).findByLabelText("Secret key") as HTMLInputElement).value).toBe(SECRET);
    expect(panel.textContent).toContain("Shown once: copy it now");
    expect(await screen.findByText("Made the key Occupella AI.")).toBeTruthy();
    expect(posted(fetch)).toEqual([["/api/ai/keys", { name: "Occupella AI" }]]);

    // Closed, the secret is gone for good: the list shows only its first characters.
    fireEvent.click(within(panel).getByRole("button", { name: "Done" }));
    expect(screen.queryByRole("dialog")).toBeNull();
    const row = await screen.findByRole("row", { name: "Occupella AI" });
    expect(row.textContent).toContain("occ_made-up-…");
    expect(screen.queryByText(SECRET)).toBeNull();
    expect(screen.queryByDisplayValue(SECRET)).toBeNull();
  });

  it("takes a standing key back once confirmed", async () => {
    const fetch = server([STANDING]);
    render(<AiKeysPage me={ADMIN} />);
    fireEvent.click(await screen.findByRole("button", { name: "Actions for Reporting script" }));
    fireEvent.click(await screen.findByRole("menuitem", { name: "Take back" }));

    const question = screen.getByRole("alertdialog");
    expect(question.textContent).toContain("Take back the key Reporting script? Software using it is refused from its next request");
    fireEvent.click(within(question).getByRole("button", { name: "Take back" }));

    expect(await screen.findByText("Took back the key Reporting script.")).toBeTruthy();
    expect(within(await screen.findByRole("row", { name: "Reporting script" })).getByText("Taken back")).toBeTruthy();
    expect(posted(fetch)).toEqual([["/api/ai/keys/k1/revoke", {}]]);
  });

  it("says why a key was refused", async () => {
    server([STANDING], "this key was taken back already");
    render(<AiKeysPage me={ADMIN} />);
    fireEvent.click(await screen.findByRole("button", { name: "Actions for Reporting script" }));
    fireEvent.click(await screen.findByRole("menuitem", { name: "Take back" }));
    fireEvent.click(within(screen.getByRole("alertdialog")).getByRole("button", { name: "Take back" }));

    expect((await screen.findByRole("alert")).textContent).toBe("this key was taken back already");
  });

  it("shows staff who aren't admins no keys, and asks the server for none", () => {
    const fetch = server([STANDING]);
    render(<AiKeysPage me={CLERK} />);

    expect(screen.getByText("Only an admin sees, makes and takes back API keys.")).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Make a key" })).toBeNull();
    expect(fetch).not.toHaveBeenCalled();
  });
});
