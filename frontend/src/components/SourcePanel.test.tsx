import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { OpensSource, SourcePanel } from "./SourcePanel";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

const PAYMENT = {
  kind: "payment",
  title: "Tenant payment",
  rows: [
    { label: "Received from", value: "Tenant 1" },
    { label: "Amount", value: "$1,550.00" },
    { label: "How", value: "Check No. 311" },
  ],
  links: [{ label: "Receipt (PDF)", url: "/api/owner/statement/sources/pglt_1/receipt" }],
};

function server(answer: unknown, status = 200) {
  const fetch = vi.fn(async () => new Response(JSON.stringify(answer), { status }));
  vi.stubGlobal("fetch", fetch);
  return fetch;
}

describe("SourcePanel", () => {
  it("shows where a line came from: its title, its rows and its files", async () => {
    const fetch = server(PAYMENT);
    render(<SourcePanel url="/api/owner/statement/sources/pglt_1" onClose={() => {}} />);

    const panel = await screen.findByRole("dialog", { name: "Tenant payment" });
    expect(fetch).toHaveBeenCalledWith("/api/owner/statement/sources/pglt_1", expect.anything());
    const terms = within(panel).getAllByRole("term").map((t) => t.textContent);
    const values = within(panel).getAllByRole("definition").map((d) => d.textContent);
    expect(terms).toEqual(["Received from", "Amount", "How"]);
    expect(values).toEqual(["Tenant 1", "$1,550.00", "Check No. 311"]);
    const receipt = within(panel).getByRole("link", { name: "Receipt (PDF)" });
    expect(receipt.getAttribute("href")).toBe("/api/owner/statement/sources/pglt_1/receipt");
  });

  it("says why when the line can't be opened", async () => {
    server({ detail: "no such line of your statement" }, 404);
    render(<SourcePanel url="/api/owner/statement/sources/pglt_9" onClose={() => {}} />);

    expect(await screen.findByText("no such line of your statement")).toBeTruthy();
    expect(screen.getByRole("dialog", { name: "Details" })).toBeTruthy();
  });

  it("closes", async () => {
    server(PAYMENT);
    const onClose = vi.fn();
    render(<SourcePanel url="/x" onClose={onClose} />);

    fireEvent.click(await screen.findByRole("button", { name: "Close" }));

    expect(onClose).toHaveBeenCalled();
  });
});

describe("OpensSource", () => {
  it("is a button that opens a dialog when the line has a source", () => {
    const onOpen = vi.fn();
    render(<OpensSource onOpen={onOpen}>Rent</OpensSource>);

    const button = screen.getByRole("button", { name: "Rent" });
    expect(button.getAttribute("aria-haspopup")).toBe("dialog");
    fireEvent.click(button);
    expect(onOpen).toHaveBeenCalled();
  });

  it("is plain text without one", () => {
    render(<OpensSource onOpen={null}>Opening balance</OpensSource>);

    expect(screen.queryByRole("button")).toBeNull();
    expect(screen.getByText("Opening balance")).toBeTruthy();
  });
});
