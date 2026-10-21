import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { VendorLinkCard } from "./VendorLinkCard";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

// Answers the link's state (made or not), a new link, and a revocation; records what was posted.
function server(madeAt: string | null) {
  let made = madeAt;
  const fetch = vi.fn(async (path: string, init?: RequestInit) => {
    if (init?.method === "POST" && path.endsWith("/revoke")) {
      made = null;
      return new Response(null, { status: 204 });
    }
    if (init?.method === "POST") {
      made = "2026-10-18T09:00:00Z";
      return new Response(JSON.stringify({ url: "http://pms.example/vendor#a-token-of-enough-length" }), { status: 201 });
    }
    return new Response(JSON.stringify({ made_at: made }));
  });
  vi.stubGlobal("fetch", fetch);
  return fetch;
}

const posted = (fetch: ReturnType<typeof server>) =>
  fetch.mock.calls.filter(([, init]) => init?.method === "POST").map(([path]) => path);

describe("VendorLinkCard", () => {
  it("makes a link, shows it once, and says one stands", async () => {
    const fetch = server(null);
    render(<VendorLinkCard vendorId="v1" name="Vendor 1" />);
    expect((await screen.findByTestId("vendor-link")).textContent).toContain("Vendor 1 has no link.");
    expect(screen.queryByRole("button", { name: "Take back" })).toBeNull();

    fireEvent.click(screen.getByRole("button", { name: "Make a link" }));
    expect(await screen.findByText("Link made; it is below, shown once.")).toBeTruthy();
    expect((screen.getByLabelText("Work order link, Vendor 1") as HTMLInputElement).value).toBe(
      "http://pms.example/vendor#a-token-of-enough-length",
    );
    // The card reloads its state after the save.
    await vi.waitFor(() =>
      expect(screen.getByTestId("vendor-link").textContent).toContain("Vendor 1 has a link, made 10/18/2026."),
    );
    expect(posted(fetch)).toEqual(["/api/vendors/v1/link"]);
  });

  it("replaces a standing link, and takes it back once confirmed", async () => {
    const fetch = server("2026-10-01T09:00:00Z");
    render(<VendorLinkCard vendorId="v1" name="Vendor 1" />);
    expect((await screen.findByTestId("vendor-link")).textContent).toContain("made 10/01/2026");

    fireEvent.click(screen.getByRole("button", { name: "Make a new link" }));
    expect(await screen.findByText("A new link replaces the old one; it is below, shown once.")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Take back" }));
    expect(screen.getByText("Take back Vendor 1's link? It will open nothing from now on.")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
    expect(posted(fetch)).toEqual(["/api/vendors/v1/link"]);
    fireEvent.click(screen.getByRole("button", { name: "Take back" }));
    fireEvent.click(screen.getAllByRole("button", { name: "Take back" }).at(-1) as HTMLElement);
    expect(await screen.findByText("Vendor 1's link is taken back.")).toBeTruthy();
    // The link shown is gone with it.
    expect(screen.queryByLabelText("Work order link, Vendor 1")).toBeNull();
    expect(posted(fetch)).toEqual(["/api/vendors/v1/link", "/api/vendors/v1/link/revoke"]);
  });

  it("says when its state can't be read", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({ detail: "sign in first" }), { status: 401 })));
    render(<VendorLinkCard vendorId="v1" name="Vendor 1" />);
    expect((await screen.findByRole("alert")).textContent).toBe("sign in first");
  });
});
