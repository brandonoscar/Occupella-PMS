import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ScopePicker } from "./ScopePicker";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

const GROUPS = {
  groups: [{ id: "g1", name: "North portfolio", properties: [] }],
  properties: [{ id: "p1", name: "Birch Flats" }, { id: "p2", name: "Cedar House" }],
};

function server(answer: unknown, status = 200) {
  vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify(answer), { status })));
}

describe("ScopePicker", () => {
  it("offers all properties, then the groups, then each property", async () => {
    server(GROUPS);
    const onChange = vi.fn();
    render(<ScopePicker scope={{}} onChange={onChange} />);
    const picker = screen.getByRole("combobox", { name: "Properties shown" });
    await within(picker).findByRole("option", { name: "North portfolio" });
    expect(within(picker).getAllByRole("group").map((g) => g.getAttribute("label"))).toEqual([
      "Property groups", "Properties",
    ]);
    fireEvent.change(picker, { target: { value: "group:g1" } });
    fireEvent.change(picker, { target: { value: "property:p2" } });
    fireEvent.change(picker, { target: { value: "" } });
    expect(onChange.mock.calls).toEqual([[{ group: "g1" }], [{ property: "p2" }], [{}]]);
  });

  it("shows a choice it doesn't offer, and leaves out groups when there are none", async () => {
    server({ groups: [], properties: GROUPS.properties });
    render(<ScopePicker scope={{ property: "archived-1" }} onChange={vi.fn()} />);
    const picker = screen.getByRole("combobox", { name: "Properties shown" }) as HTMLSelectElement;
    await within(picker).findByRole("option", { name: "A property" });
    expect(picker.value).toBe("property:archived-1");
    expect(within(picker).getAllByRole("group").map((g) => g.getAttribute("label"))).toEqual(["Properties"]);
  });

  it("still offers all properties when its choices can't be read", async () => {
    server({ detail: "sign in first" }, 401);
    render(<ScopePicker scope={{ group: "g9" }} onChange={vi.fn()} />);
    const picker = screen.getByRole("combobox", { name: "Properties shown" });
    await within(picker).findByRole("option", { name: "A property group" });
    expect(within(picker).getByRole("option", { name: "All properties" })).toBeTruthy();
  });
});
