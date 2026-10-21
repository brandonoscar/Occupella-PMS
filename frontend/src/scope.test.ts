import { act, renderHook } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { scopeOf, scopeParams, useScope } from "./scope";

afterEach(() => {
  window.history.pushState(null, "", "/");
  window.localStorage.clear();
  vi.restoreAllMocks();
});

describe("scope", () => {
  it("reads a group or a property from the URL, a group first", () => {
    expect(scopeOf("?group=g1")).toEqual({ group: "g1" });
    expect(scopeOf("?property=p1&page=2")).toEqual({ property: "p1" });
    expect(scopeOf("?group=g1&property=p1")).toEqual({ group: "g1" });
    expect(scopeOf("")).toEqual({});
    expect(scopeParams({ property: "p1" })).toEqual({ property: "p1" });
    expect(scopeParams({})).toEqual({});
  });

  it("keeps the choice in the URL and in this browser, so the next page opens on it", () => {
    window.history.pushState(null, "", "/properties?page=1#top");
    const { result, unmount } = renderHook(() => useScope());
    expect(result.current[0]).toEqual({});
    act(() => result.current[1]({ group: "g1" }));
    expect(window.location.pathname + window.location.search + window.location.hash).toBe(
      "/properties?page=1&group=g1#top",
    );
    unmount();

    // The next list page, opened from the menu without it in its URL.
    window.history.pushState(null, "", "/rent-roll");
    const next = renderHook(() => useScope());
    expect(next.result.current[0]).toEqual({ group: "g1" });
    expect(window.location.search).toBe("?group=g1");
    // A link that names a property wins over what was kept.
    next.unmount();
    window.history.pushState(null, "", "/tenants?property=p9");
    expect(renderHook(() => useScope()).result.current[0]).toEqual({ property: "p9" });
    act(() => undefined);
    expect(JSON.parse(window.localStorage.getItem("pms.scope") ?? "{}")).toEqual({ property: "p9" });
  });

  it("works with nothing to keep it in", () => {
    vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => {
      throw new Error("storage is off");
    });
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
      throw new Error("storage is off");
    });
    const { result } = renderHook(() => useScope());
    expect(result.current[0]).toEqual({});
    act(() => result.current[1]({ property: "p1" }));
    expect(window.location.search).toBe("?property=p1");
  });

  it("ignores what it can't read back", () => {
    window.localStorage.setItem("pms.scope", "not json");
    expect(renderHook(() => useScope()).result.current[0]).toEqual({});
    window.localStorage.setItem("pms.scope", JSON.stringify({ property: "p2" }));
    expect(renderHook(() => useScope()).result.current[0]).toEqual({ property: "p2" });
  });
});
