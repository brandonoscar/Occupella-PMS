import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { Me } from "../api";
import { holdBack } from "../holdBack";
import type { Paged } from "../rentals";
import type { VendorRow } from "../vendors";
import { standingTone, standingWord } from "../vendors";
import { VendorsPage } from "./VendorsPage";

beforeEach(() => window.history.replaceState(null, "", "/vendors"));
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

const ME: Me = {
  kind: "staff", email: "staff@example.com", name: "Staff 1", person_id: "s1", pmc_id: "p1",
  pmc_name: "Synthetic PMC",
};

const ROWS: VendorRow[] = [
  { id: "v1", name: "Vendor 1", trade: "Plumbing", phone: "555-555-0100", email: "vendor1@example.com",
    w9_on_file: true, insurance: "expires in 15 days" },
  { id: "v2", name: "Vendor 2", trade: null, phone: null, email: null, w9_on_file: false,
    insurance: "no insurance on file" },
];
const PAGE: Paged<VendorRow> = { items: ROWS, total: 2, page: 1, per_page: 50 };

function server(answers: Record<string, unknown> = {}, refuse = "") {
  const fetch = vi.fn(async (path: string, init?: RequestInit) => {
    if (init?.method === "POST") {
      if (refuse) return new Response(JSON.stringify({ detail: refuse }), { status: 422 });
      return new Response(JSON.stringify({ id: "v9" }), { status: 201 });
    }
    return new Response(JSON.stringify(path in answers ? answers[path] : PAGE), { status: 200 });
  });
  vi.stubGlobal("fetch", fetch);
  return fetch;
}

const posted = (fetch: ReturnType<typeof server>) =>
  fetch.mock.calls
    .filter(([, init]) => init?.method === "POST")
    .map(([path, init]) => [path, JSON.parse(String(init?.body))]);

describe("standingWord and standingTone", () => {
  it("read a standing as the list does", () => {
    expect(standingWord("current")).toBe("Current");
    expect(standingWord("expires in 3 days")).toBe("Expires in 3 days");
    expect(standingWord("no insurance on file")).toBe("None on file");
    expect(standingTone("current")).toBe("positive");
    expect(standingTone("expired")).toBe("danger");
    expect(standingTone("expires today")).toBe("caution");
    expect(standingTone("no insurance on file")).toBe("neutral");
  });
});

describe("VendorsPage", () => {
  it("lists each vendor's trade, phone, email, W-9 and insurance; a row opens their page", async () => {
    server();
    render(<VendorsPage me={ME} />);

    const row = await screen.findByRole("row", { name: "Vendor 1" });
    expect(within(row).getAllByRole("cell").map((c) => c.textContent)).toEqual([
      "Vendor 1", "Plumbing", "555-555-0100", "vendor1@example.com", "On file", "Expires in 15 days",
    ]);
    expect(within(screen.getByRole("row", { name: "Vendor 2" })).getAllByRole("cell").map((c) => c.textContent)).toEqual([
      "Vendor 2", "", "", "", "Missing", "None on file",
    ]);
    fireEvent.click(row);
    expect(window.location.pathname).toBe("/vendors/v1");
  });

  it("goes a page at a time", async () => {
    const fetch = server({
      "/api/vendors?page=1": { ...PAGE, total: 51 },
      "/api/vendors?page=2": { ...PAGE, items: [ROWS[1]], page: 2, total: 51 },
    });
    render(<VendorsPage me={ME} />);
    await screen.findByRole("row", { name: "Vendor 1" });
    fireEvent.click(screen.getByRole("button", { name: "Next" }));
    await vi.waitFor(() => expect(screen.queryByRole("row", { name: "Vendor 1" })).toBeNull());
    expect(fetch.mock.calls.map(([p]) => p)).toEqual(["/api/vendors?page=1", "/api/vendors?page=2"]);
  });

  it("shows the page of vendors asked for now, even when an earlier page's answer comes back last", async () => {
    server({
      "/api/vendors?page=1": { ...PAGE, items: [ROWS[0]], total: 151 },
      "/api/vendors?page=2": { ...PAGE, items: [ROWS[1]], page: 2, total: 151 },
      "/api/vendors?page=3": { ...PAGE, items: [{ ...ROWS[0], id: "v3", name: "Vendor 3" }], page: 3, total: 151 },
    });
    const release = holdBack((path) => path === "/api/vendors?page=3");
    render(<VendorsPage me={ME} />);
    await screen.findByRole("row", { name: "Vendor 1" });
    fireEvent.click(screen.getByRole("button", { name: "Next" }));
    await screen.findByRole("row", { name: "Vendor 2" });

    fireEvent.click(screen.getByRole("button", { name: "Next" }));
    fireEvent.click(screen.getByRole("button", { name: "Previous" }));
    await screen.findByRole("row", { name: "Vendor 1" });
    await release();

    expect(screen.getByRole("row", { name: "Vendor 1" })).toBeTruthy();
    expect(screen.queryByRole("row", { name: "Vendor 3" })).toBeNull();
  });

  it("adds a vendor with their trade, 1099, how they are paid and contact, then opens their page", async () => {
    const fetch = server();
    render(<VendorsPage me={ME} />);
    await screen.findByRole("row", { name: "Vendor 1" });

    fireEvent.click(screen.getByRole("button", { name: "Add vendor" }));
    const panel = screen.getByRole("dialog", { name: "Add vendor" });
    fireEvent.change(within(panel).getByLabelText("Vendor's name"), { target: { value: "Vendor 3" } });
    fireEvent.change(within(panel).getByLabelText("Trade (optional)"), { target: { value: " Electrical " } });
    fireEvent.change(within(panel).getByLabelText("Paid by"), { target: { value: "EFT" } });
    fireEvent.click(within(panel).getByLabelText("1099 vendor"));
    fireEvent.change(within(panel).getByLabelText("Email (optional)"), { target: { value: " vendor3@example.com " } });
    fireEvent.click(within(panel).getByRole("button", { name: "Add vendor" }));
    await vi.waitFor(() => expect(window.location.pathname).toBe("/vendors/v9"));

    expect(posted(fetch)).toEqual([[
      "/api/vendors",
      { name: "Vendor 3", trade: "Electrical", gets_1099: true, payment_method: "EFT", contact: { email: "vendor3@example.com" } },
    ]]);
  });

  it("says why a vendor was refused, when there are none, and when they can't be read", async () => {
    server({ "/api/vendors?page=1": { ...PAGE, items: [], total: 0 } }, "check what you entered and try again");
    render(<VendorsPage me={ME} />);
    expect((await screen.findByTestId("empty-state")).textContent).toContain("No vendors yet");
    fireEvent.click(screen.getByRole("button", { name: "Add vendor" }));
    const panel = screen.getByRole("dialog", { name: "Add vendor" });
    fireEvent.change(within(panel).getByLabelText("Vendor's name"), { target: { value: "Vendor 3" } });
    fireEvent.click(within(panel).getByRole("button", { name: "Add vendor" }));
    expect((await within(panel).findByRole("alert")).textContent).toBe("check what you entered and try again");
    cleanup();

    vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({ detail: "sign in first" }), { status: 401 })));
    render(<VendorsPage me={ME} />);
    expect((await screen.findByRole("alert")).textContent).toBe("sign in first");
  });
});
