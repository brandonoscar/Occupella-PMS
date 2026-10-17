import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { Me } from "../api";
import type { Member } from "../staff";
import { StaffPage } from "./StaffPage";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

const ME: Me = {
  kind: "staff",
  email: "admin@example.com",
  name: "Admin 1",
  person_id: "s1",
  pmc_id: "p1",
  pmc_name: "Synthetic PMC",
  roles: ["admin", "approve", "disburse"],
};

const MEMBERS: Member[] = [
  { staff_id: "s1", name: "Admin 1", roles: ["admin", "approve", "disburse"] },
  { staff_id: "s2", name: "Clerk 1", roles: [] },
];

const PEOPLE = [
  { kind: "staff", person_id: "s1", name: "Admin 1", email: "admin@example.com", login_id: "l1", status: "active" },
  { kind: "staff", person_id: "s2", name: "Clerk 1", email: null, login_id: null, status: "no login" },
  { kind: "owner", person_id: "o1", name: "Owner 1", email: "owner@example.com", login_id: "l2", status: "active" },
];

function server(refuse: Record<string, string> = {}) {
  const fetch = vi.fn(async (path: string, init?: RequestInit) => {
    if (init?.method !== "POST") {
      const body =
        path === "/api/custom-roles" ? { areas: [], roles: [] } : path === "/api/signers" ? [] : path === "/api/people" ? PEOPLE : MEMBERS;
      return new Response(JSON.stringify(body), { status: 200 });
    }
    if (path in refuse) return new Response(JSON.stringify({ detail: refuse[path] }), { status: 409 });
    if (path === "/api/invites") {
      return new Response(JSON.stringify({ login_id: "l9", url: "https://pms.example/invite#tok", expires_at: "2026-10-24T00:00:00Z" }), { status: 201 });
    }
    return path.endsWith("/roles")
      ? new Response(null, { status: 204 })
      : new Response(JSON.stringify({ id: "s3" }), { status: 201 });
  });
  vi.stubGlobal("fetch", fetch);
  return fetch;
}

const posted = (fetch: ReturnType<typeof server>) =>
  fetch.mock.calls
    .filter(([, init]) => init?.method === "POST")
    .map(([path, init]) => [path, JSON.parse(String(init?.body))]);

describe("StaffPage", () => {
  it("shows who may do what, and an admin changes a role and adds a staff member", async () => {
    const fetch = server();
    render(<StaffPage me={ME} />);
    const disburse = (await screen.findByLabelText("Clerk 1: Pay out")) as HTMLInputElement;
    expect(disburse.checked).toBe(false);
    expect((screen.getByLabelText("Admin 1: Admin") as HTMLInputElement).checked).toBe(true);

    fireEvent.click(disburse);
    expect(await screen.findByText("Clerk 1 may now: Pay out.")).toBeTruthy();
    fireEvent.click(screen.getByLabelText("Admin 1: Approve"));
    expect(await screen.findByText("Admin 1 may no longer: Approve.")).toBeTruthy();
    fireEvent.change(screen.getByLabelText("Name"), { target: { value: "Clerk 2" } });
    fireEvent.click(screen.getByRole("button", { name: "Add" }));
    expect(await screen.findByText("Added Clerk 2; invite them from their row.")).toBeTruthy();

    expect(posted(fetch)).toEqual([
      ["/api/staff/s2/roles", { role: "disburse", granted: true }],
      ["/api/staff/s1/roles", { role: "approve", granted: false }],
      ["/api/staff", { name: "Clerk 2" }],
    ]);
    expect((screen.getByLabelText("Name") as HTMLInputElement).value).toBe("");
  });

  it("shows each staff member's login, and an admin invites one to sign in from a side panel", async () => {
    const fetch = server();
    render(<StaffPage me={ME} />);
    await screen.findByLabelText("Clerk 1: Pay out");
    const row = (name: string) => screen.getByLabelText(`${name}: Admin`).closest("tr") as HTMLElement;
    await vi.waitFor(() => expect(within(row("Clerk 1")).getByText("No login")).toBeTruthy());
    expect(within(row("Admin 1")).getByText("Active")).toBeTruthy();
    // Nobody invites themself.
    expect(within(row("Admin 1")).queryByRole("button", { name: "Invite" })).toBeNull();

    fireEvent.click(within(row("Clerk 1")).getByRole("button", { name: "Invite" }));
    const panel = screen.getByRole("dialog", { name: "Invite Clerk 1" });
    fireEvent.change(within(panel).getByLabelText("Email to sign in with"), { target: { value: "clerk1@example.com" } });
    fireEvent.click(within(panel).getByRole("button", { name: "Invite to sign in" }));
    expect(await screen.findByText("Invite link made for clerk1@example.com; give it to Clerk 1.")).toBeTruthy();
    expect((within(panel).getByLabelText("Invite link, Clerk 1") as HTMLInputElement).value).toBe("https://pms.example/invite#tok");
    expect(posted(fetch)).toEqual([["/api/invites", { kind: "staff", person_id: "s2", email: "clerk1@example.com" }]]);
  });

  it("shows a refusal, and only an admin changes anything", async () => {
    server({ "/api/staff/s1/roles": "staff member s1 is the PMC's last admin" });
    render(<StaffPage me={ME} />);
    fireEvent.click(await screen.findByLabelText("Admin 1: Admin"));
    expect(await screen.findByText("staff member s1 is the PMC's last admin")).toBeTruthy();
    cleanup();

    server();
    render(<StaffPage me={{ ...ME, roles: [] }} />);
    expect(((await screen.findByLabelText("Clerk 1: Admin")) as HTMLInputElement).disabled).toBe(true);
    expect(screen.getByText("Only an admin changes roles.")).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Add" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Invite" })).toBeNull();
    cleanup();

    vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({ detail: "sign in first" }), { status: 401 })));
    render(<StaffPage me={ME} />);
    expect((await screen.findAllByRole("alert"))[0]?.textContent).toBe("sign in first");
  });
});
