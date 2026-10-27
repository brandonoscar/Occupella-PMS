import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { OnFile } from "../taxIds";
import { nineDigits, taxIdLine } from "../taxIds";
import { TaxIdCard } from "./TaxIdCard";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

// Synthetic tax IDs only: the never-issued forms 00-ddddddd and 000-dd-dddd.
const HELD = { id: "t1", kind: "EIN" as const, last4: "4567", recorded_at: "2026-10-25T00:00:00Z", recorded_by: "Staff 1" };

function server(onFile: OnFile, refuse?: string) {
  let now = onFile;
  const fetch = vi.fn(async (path: string, init?: RequestInit) => {
    if (init?.method === "POST") {
      if (refuse) return new Response(JSON.stringify({ detail: refuse }), { status: 409 });
      const body = JSON.parse(String(init.body));
      now = { ...now, tax_id: { ...HELD, id: "t2", kind: body.kind, last4: body.number.slice(-4) } };
      return new Response(JSON.stringify(now), { status: 201 });
    }
    if (path.endsWith("/whole")) return new Response(JSON.stringify({ kind: "EIN", number: "00-1234567" }), { status: 200 });
    return new Response(JSON.stringify(now), { status: 200 });
  });
  vi.stubGlobal("fetch", fetch);
  return fetch;
}

const posts = (fetch: ReturnType<typeof server>) =>
  fetch.mock.calls.filter(([, init]) => init?.method === "POST").map(([path, init]) => [path, JSON.parse(String(init?.body))]);

describe("taxIds", () => {
  it("writes a tax ID by its last four, as its kind is written", () => {
    expect(taxIdLine({ kind: "EIN", last4: "4567" })).toBe("EIN ••-•••4567");
    expect(taxIdLine({ kind: "SSN", last4: "3456" })).toBe("SSN •••-••-3456");
  });

  it("takes nine digits, with dashes and spaces", () => {
    expect(nineDigits("00-1234567")).toBe("001234567");
    expect(nineDigits(" 000 12 3456 ")).toBe("000123456");
    expect(nineDigits("12345")).toBeNull();
    expect(nineDigits("00-123456a")).toBeNull();
  });
});

describe("TaxIdCard", () => {
  it("shows the last four, and an admin reveals it whole until they hide it", async () => {
    const fetch = server({ tax_id: HELD, may_reveal: true });
    render(<TaxIdCard kind="vendor" id="v1" name="Vendor 1" />);
    expect((await screen.findByTestId("tax-id")).textContent).toBe("EIN ••-•••4567");
    expect(screen.getByText("10/25/2026 by Staff 1")).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: "Reveal" }));
    expect(await screen.findByText("EIN 00-1234567")).toBeTruthy();
    expect(screen.getByText("Shown to you whole, and recorded with your name.")).toBeTruthy();
    expect(fetch.mock.calls.map(([path]) => path)).toContain("/api/tax-ids/t1/whole");

    fireEvent.click(screen.getByRole("button", { name: "Hide" }));
    expect(screen.getByTestId("tax-id").textContent).toBe("EIN ••-•••4567");
  });

  it("offers no Reveal to staff who aren't admins", async () => {
    server({ tax_id: HELD, may_reveal: false });
    render(<TaxIdCard kind="owner" id="o1" name="Owner 1" />);
    expect((await screen.findByTestId("tax-id")).textContent).toBe("EIN ••-•••4567");
    expect(screen.queryByRole("button", { name: "Reveal" })).toBeNull();
    expect(screen.getByText("Only an admin sees a tax ID whole.")).toBeTruthy();
  });

  it("adds one, sending its nine digits, and shows its last four", async () => {
    const fetch = server({ tax_id: null, may_reveal: true });
    render(<TaxIdCard kind="owner" id="o1" name="Owner 1" />);
    expect((await screen.findByTestId("tax-id")).textContent).toBe("None on file");
    fireEvent.click(screen.getByRole("button", { name: "Add tax ID" }));
    const panel = screen.getByRole("dialog", { name: "Tax ID, Owner 1" });
    fireEvent.change(within(panel).getByLabelText("Kind"), { target: { value: "SSN" } });
    fireEvent.change(within(panel).getByLabelText("Number"), { target: { value: "000-12-3456" } });
    fireEvent.click(within(panel).getByRole("button", { name: "Save" }));

    expect(await screen.findByText("Saved Owner 1's tax ID.")).toBeTruthy();
    expect((await screen.findByTestId("tax-id")).textContent).toBe("SSN •••-••-3456");
    expect(posts(fetch)).toEqual([["/api/tax-ids/owners/o1", { kind: "SSN", number: "000123456" }]]);
  });

  it("says what a tax ID is before sending one that isn't nine digits", async () => {
    const fetch = server({ tax_id: null, may_reveal: true });
    render(<TaxIdCard kind="vendor" id="v1" name="Vendor 1" />);
    fireEvent.click(await screen.findByRole("button", { name: "Add tax ID" }));
    const panel = screen.getByRole("dialog", { name: "Tax ID, Vendor 1" });
    fireEvent.change(within(panel).getByLabelText("Number"), { target: { value: "12345" } });
    fireEvent.click(within(panel).getByRole("button", { name: "Save" }));

    expect(await within(panel).findByText("A tax ID is nine digits: XX-XXXXXXX for an EIN, XXX-XX-XXXX for an SSN.")).toBeTruthy();
    expect(posts(fetch)).toEqual([]);
  });

  it("says why a server refused it", async () => {
    server({ tax_id: null, may_reveal: true }, "this server keeps no tax IDs: it has no PMS_DATA_KEY; ask whoever runs it to set one");
    render(<TaxIdCard kind="vendor" id="v1" name="Vendor 1" />);
    fireEvent.click(await screen.findByRole("button", { name: "Add tax ID" }));
    const panel = screen.getByRole("dialog", { name: "Tax ID, Vendor 1" });
    fireEvent.change(within(panel).getByLabelText("Number"), { target: { value: "00-1234567" } });
    fireEvent.click(within(panel).getByRole("button", { name: "Save" }));
    expect(await within(panel).findByText(/this server keeps no tax IDs/)).toBeTruthy();
  });
});
