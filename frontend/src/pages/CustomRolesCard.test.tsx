import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { CustomRoles } from "../customRoles";
import type { Member } from "../staff";
import { CustomRolesCard } from "./CustomRolesCard";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

const MEMBERS: Member[] = [
  { staff_id: "s1", name: "Admin 1", roles: ["admin"] },
  { staff_id: "s2", name: "Clerk 1", roles: [] },
];
const ROLES: CustomRoles = {
  areas: [
    { area: "rent", label: "rent" },
    { area: "bills", label: "bills" },
    { area: "journal", label: "the journal" },
  ],
  roles: [{ id: "r1", name: "Bookkeeping", areas: ["journal"], staff: [{ staff_id: "s2", name: "Clerk 1" }] }],
};

function server(roles: CustomRoles = ROLES, refuse = "") {
  const fetch = vi.fn(async (_path: string, init?: RequestInit) => {
    if (init?.method !== "POST") return new Response(JSON.stringify(roles), { status: 200 });
    if (refuse) return new Response(JSON.stringify({ detail: refuse }), { status: 403 });
    return init.body && String(init.body).includes('"name"')
      ? new Response(JSON.stringify({ id: "r2" }), { status: 201 })
      : new Response(null, { status: 204 });
  });
  vi.stubGlobal("fetch", fetch);
  return fetch;
}

const posted = (fetch: ReturnType<typeof server>) =>
  fetch.mock.calls
    .filter(([, init]) => init?.method === "POST")
    .map(([path, init]) => [path, JSON.parse(String(init?.body))]);

describe("CustomRolesCard", () => {
  it("shows each role's areas and holders, and an admin changes both and makes a role", async () => {
    const fetch = server();
    render(<CustomRolesCard members={MEMBERS} admin />);
    const role = await screen.findByRole("listitem", { name: "Bookkeeping" });

    expect(role.textContent).toContain("Bookkeeping may change: the journal.");
    expect((within(role).getByLabelText("Bookkeeping: the journal") as HTMLInputElement).checked).toBe(true);
    expect((within(role).getByLabelText("Clerk 1 holds Bookkeeping") as HTMLInputElement).checked).toBe(true);
    expect((within(role).getByLabelText("Admin 1 holds Bookkeeping") as HTMLInputElement).checked).toBe(false);

    fireEvent.click(within(role).getByLabelText("Bookkeeping: bills"));
    expect(await screen.findByText("Bookkeeping may now change bills.")).toBeTruthy();
    fireEvent.click(within(role).getByLabelText("Clerk 1 holds Bookkeeping"));
    expect(await screen.findByText("Clerk 1 no longer holds Bookkeeping.")).toBeTruthy();
    fireEvent.change(screen.getByLabelText("New role"), { target: { value: "Leasing agent" } });
    fireEvent.click(screen.getByRole("button", { name: "Make role" }));
    expect(await screen.findByText("Made Leasing agent: choose what it may change, then who holds it.")).toBeTruthy();

    expect(posted(fetch)).toEqual([
      ["/api/custom-roles/r1/areas", { area: "bills", granted: true }],
      ["/api/custom-roles/r1/staff", { staff_id: "s2", granted: false }],
      ["/api/custom-roles", { name: "Leasing agent", areas: [] }],
    ]);
  });

  it("lets anyone else look, not change, and shows a refusal", async () => {
    server(ROLES, "Clerk 1 may not change a custom role (the admin role)");
    render(<CustomRolesCard members={MEMBERS} admin={false} />);
    const role = await screen.findByRole("listitem", { name: "Bookkeeping" });

    expect((within(role).getByLabelText("Bookkeeping: rent") as HTMLInputElement).disabled).toBe(true);
    expect(screen.queryByRole("button", { name: "Make role" })).toBeNull();
    cleanup();

    server(ROLES, "Clerk 1 may not change a custom role (the admin role)");
    render(<CustomRolesCard members={MEMBERS} admin />);
    fireEvent.click(within(await screen.findByRole("listitem", { name: "Bookkeeping" })).getByLabelText("Bookkeeping: rent"));
    expect((await screen.findByRole("alert")).textContent).toBe("Clerk 1 may not change a custom role (the admin role)");
  });

  it("says when there are none yet", async () => {
    server({ ...ROLES, roles: [] });
    render(<CustomRolesCard members={MEMBERS} admin />);
    expect((await screen.findByTestId("empty-state")).textContent).toContain("No custom roles yet");
  });
});
