import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { Me } from "../api";
import { GroupsPage } from "./GroupsPage";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

const ME: Me = {
  kind: "staff", email: "staff@example.com", name: "Staff 1", person_id: "s1", pmc_id: "p1",
  pmc_name: "Synthetic PMC",
};

const GROUPS = {
  groups: [{ id: "g1", name: "North", properties: [{ id: "p1", name: "Birch Flats" }, { id: "p9", name: "Old Mill" }] }],
  properties: [{ id: "p1", name: "Birch Flats" }, { id: "p2", name: "Cedar House" }],
};

function server(groups: unknown = GROUPS, refuse: Record<string, string> = {}) {
  const fetch = vi.fn(async (path: string, init?: RequestInit) => {
    if (init?.method === "POST") {
      if (path in refuse) return new Response(JSON.stringify({ detail: refuse[path] }), { status: 409 });
      return path === "/api/property-groups"
        ? new Response(JSON.stringify({ id: "g2" }), { status: 201 })
        : new Response(null, { status: 204 });
    }
    return new Response(JSON.stringify(groups), { status: 200 });
  });
  vi.stubGlobal("fetch", fetch);
  return fetch;
}

const posted = (fetch: ReturnType<typeof server>) =>
  fetch.mock.calls.filter(([, init]) => init?.method === "POST").map(([path, init]) => [path, JSON.parse(String(init?.body))]);

describe("GroupsPage", () => {
  it("lists the groups with their properties, and adds one in a side panel", async () => {
    const fetch = server();
    render(<GroupsPage me={ME} />);
    const north = await screen.findByRole("row", { name: "North" });
    expect(within(north).getAllByRole("cell").map((c) => c.textContent)).toEqual(["North", "Birch Flats, Old Mill", "2"]);

    fireEvent.click(screen.getByRole("button", { name: "Add group" }));
    const panel = screen.getByRole("dialog", { name: "Add group" });
    fireEvent.change(within(panel).getByLabelText("Name"), { target: { value: "South" } });
    fireEvent.click(within(panel).getByLabelText("Cedar House"));
    fireEvent.click(within(panel).getByRole("button", { name: "Add group" }));
    expect(await screen.findByText("Added the group South, with 1 property.")).toBeTruthy();
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(posted(fetch)).toEqual([["/api/property-groups", { name: "South", property_ids: ["p2"] }]]);
  });

  it("edits a group's name and properties, keeping an archived one until it is taken out", async () => {
    const fetch = server();
    render(<GroupsPage me={ME} />);
    fireEvent.click(await screen.findByRole("button", { name: "North" }));
    const panel = screen.getByRole("dialog", { name: "Edit North" });
    // Old Mill is archived, so not offered, but still in the group.
    expect((within(panel).getByLabelText("Old Mill") as HTMLInputElement).checked).toBe(true);
    fireEvent.change(within(panel).getByLabelText("Name"), { target: { value: "North portfolio" } });
    fireEvent.click(within(panel).getByLabelText("Old Mill"));
    fireEvent.click(within(panel).getByLabelText("Cedar House"));
    fireEvent.click(within(panel).getByRole("button", { name: "Save" }));
    expect(await screen.findByText("Saved North portfolio: 2 properties.")).toBeTruthy();
    expect(posted(fetch)).toEqual([
      ["/api/property-groups/g1/name", { name: "North portfolio" }],
      ["/api/property-groups/g1/properties", { property_ids: ["p1", "p2"] }],
    ]);
  });

  it("saves only the properties when the name is the same, and says why one is refused", async () => {
    const fetch = server(GROUPS, { "/api/property-groups/g1/properties": "Birch Flats is already in North" });
    render(<GroupsPage me={ME} />);
    fireEvent.click(await screen.findByRole("row", { name: "North" }));
    const panel = screen.getByRole("dialog", { name: "Edit North" });
    fireEvent.click(within(panel).getByRole("button", { name: "Save" }));
    expect((await within(panel).findByRole("alert")).textContent).toBe("Birch Flats is already in North");
    expect(posted(fetch).map(([path]) => path)).toEqual(["/api/property-groups/g1/properties"]);
    fireEvent.click(within(panel).getByRole("button", { name: "Cancel" }));
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("says when there are no groups yet, or no properties to put in one", async () => {
    server({ groups: [], properties: [] });
    render(<GroupsPage me={ME} />);
    expect((await screen.findByTestId("empty-state")).textContent).toContain("No property groups yet.");
    fireEvent.click(screen.getByRole("button", { name: "Add group" }));
    expect(screen.getByText("No properties yet.")).toBeTruthy();
  });

  it("says when the groups can't be read", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({ detail: "sign in first" }), { status: 401 })));
    render(<GroupsPage me={ME} />);
    expect((await screen.findByRole("alert")).textContent).toBe("sign in first");
  });
});
