import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { Me } from "../api";
import { LoginsPage } from "./LoginsPage";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

const ME: Me = {
  kind: "staff",
  email: "manager@example.com",
  name: "Manager 1",
  person_id: "s1",
  pmc_id: "p1",
  pmc_name: "Synthetic PMC",
  roles: ["admin", "approve", "disburse"],
};

const PEOPLE = [
  { kind: "staff", person_id: "s1", name: "Manager 1", email: "manager@example.com", login_id: "l1", status: "active" },
  { kind: "staff", person_id: "s2", name: "Bookkeeper 1", email: "books@example.com", login_id: "l4", status: "active" },
  { kind: "owner", person_id: "o1", name: "Owner 1", email: "owner@example.com", login_id: "l2", status: "invited" },
  { kind: "tenant", person_id: "t1", name: "Tenant 1", email: null, login_id: null, status: "no login" },
  { kind: "tenant", person_id: "t2", name: "Tenant 2", email: "t2@example.com", login_id: "l3", status: "disabled" },
];

function server() {
  const fetch = vi.fn(async (path: string, init?: RequestInit) => {
    if (init?.method === "POST") return new Response(null, { status: 204 });
    return new Response(JSON.stringify(path === "/api/people" ? PEOPLE : []), { status: 200 });
  });
  vi.stubGlobal("fetch", fetch);
  return fetch;
}

describe("LoginsPage", () => {
  it("lists everyone's role and login as a badge, each opening their own page, and invites no one", async () => {
    server();
    render(<LoginsPage me={ME} />);

    const badge = async (name: string) => {
      const cells = within(await screen.findByRole("row", { name })).getAllByRole("cell");
      const status = cells[3]!.querySelector("[data-tone]");
      return [cells[1]!.textContent, cells[2]!.textContent, status?.textContent, status?.getAttribute("data-tone")];
    };
    expect(await badge("Manager 1")).toEqual(["Staff", "manager@example.com", "Active", "positive"]);
    expect(await badge("Owner 1")).toEqual(["Owner", "owner@example.com", "Invited", "info"]);
    expect(await badge("Tenant 1")).toEqual(["Tenant", "", "No login", "neutral"]);
    expect(await badge("Tenant 2")).toEqual(["Tenant", "t2@example.com", "Disabled", "caution"]);
    const href = (name: string) => within(screen.getByRole("row", { name })).getByRole("link", { name }).getAttribute("href");
    expect([href("Owner 1"), href("Tenant 1"), href("Manager 1")]).toEqual(["/owners/o1", "/tenants/t1", "/settings/staff"]);
    expect(screen.queryByRole("button", { name: /invite/i })).toBeNull();
    expect(screen.queryByRole("textbox")).toBeNull();
  });

  it("narrows to one status", async () => {
    server();
    render(<LoginsPage me={ME} />);
    await screen.findByRole("row", { name: "Owner 1" });

    fireEvent.change(screen.getByLabelText("Status"), { target: { value: "active" } });
    expect(screen.getAllByRole("row").slice(1).map((r) => r.getAttribute("aria-label"))).toEqual(["Manager 1", "Bookkeeper 1"]);
    fireEvent.change(screen.getByLabelText("Status"), { target: { value: "invited" } });
    expect(screen.getAllByRole("row").slice(1).map((r) => r.getAttribute("aria-label"))).toEqual(["Owner 1"]);
    fireEvent.change(screen.getByLabelText("Status"), { target: { value: "" } });
    expect(screen.getAllByRole("row")).toHaveLength(6);
  });

  it("disables a login for good from its row's menu once staff say yes, never their own, a disabled one or one not made", async () => {
    const fetch = server();
    render(<LoginsPage me={ME} />);
    await screen.findByRole("row", { name: "Bookkeeper 1" });
    const posts = () => fetch.mock.calls.filter(([, init]) => init?.method === "POST").map(([path]) => path);

    for (const name of ["Manager 1", "Tenant 1", "Tenant 2"]) {
      expect(screen.queryByRole("button", { name: `Actions for ${name}` })).toBeNull();
    }
    fireEvent.click(screen.getByRole("button", { name: "Actions for Bookkeeper 1" }));
    fireEvent.click(screen.getByRole("menuitem", { name: "Disable" }));
    const asked = screen.getByRole("alertdialog");
    expect(asked.textContent).toContain("Disable Bookkeeper 1's login? They are signed out now and can't sign in again.");
    fireEvent.click(within(asked).getByRole("button", { name: "Cancel" }));
    expect(posts()).toEqual([]);

    fireEvent.click(screen.getByRole("button", { name: "Actions for Bookkeeper 1" }));
    fireEvent.click(screen.getByRole("menuitem", { name: "Disable" }));
    fireEvent.click(within(screen.getByRole("alertdialog")).getByRole("button", { name: "Disable" }));
    expect(await screen.findByText("Bookkeeper 1's login is disabled; they are signed out.")).toBeTruthy();
    expect(posts()).toEqual(["/api/logins/l4/disable"]);
  });

  it("says when nobody has that status, and why the list can't be read", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify([]), { status: 200 })));
    render(<LoginsPage me={ME} />);
    expect(await screen.findByText("No logins here")).toBeTruthy();
    cleanup();

    vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({ detail: "sign in first" }), { status: 401 })));
    render(<LoginsPage me={ME} />);
    expect((await screen.findAllByRole("alert"))[0]?.textContent).toBe("sign in first");
  });
});
