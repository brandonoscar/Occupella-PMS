import { afterEach, describe, expect, it } from "vitest";
import { navigate } from "./router";

afterEach(() => window.history.pushState(null, "", "/"));

describe("navigate", () => {
  it("adds a page to the history once, so Back goes back one page", () => {
    window.history.pushState(null, "", "/bills");
    const before = window.history.length;
    let told = 0;
    const listen = () => (told += 1);
    window.addEventListener("popstate", listen);

    // A link inside a clickable row asks for its page twice.
    navigate("/bills/b1");
    navigate("/bills/b1");
    window.removeEventListener("popstate", listen);

    expect(window.location.pathname).toBe("/bills/b1");
    expect(window.history.length).toBe(before + 1);
    expect(told).toBe(2); // the page still hears both, and shows the same page
  });

  it("keeps a page's query apart from the page without one", () => {
    window.history.pushState(null, "", "/bills");
    const before = window.history.length;
    navigate("/bills?tab=paid");

    expect(window.location.search).toBe("?tab=paid");
    expect(window.history.length).toBe(before + 1);
  });
});
