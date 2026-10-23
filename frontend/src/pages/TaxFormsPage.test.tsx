import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { Me } from "../api";
import type { TaxForms } from "./TaxFormsPage";
import { TaxFormsPage, needs, tinCut } from "./TaxFormsPage";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

const ME: Me = {
  kind: "staff", email: "staff@example.com", name: "Staff 1", person_id: "s1", pmc_id: "p1", pmc_name: "Synthetic PMC",
  roles: ["admin"],
} as Me;
const AT = "2026-10-23T15:00:00Z";
const BARE: TaxForms = {
  year: 2026, threshold: "2000.00", sealing: true,
  payer: { kind: "company", id: null, name: "Synthetic PMC", taxpayer_id: null, address: null, missing: ["taxpayer ID", "mailing address"] },
  recipients: [
    { kind: "vendor", id: "v1", name: "Synthetic Repairs", taxpayer_id: null, address: ["1 Synthetic Way", "Springfield, ZZ 00000"],
      missing: ["taxpayer ID"], form: "1099-NEC", amount: "2500.00" },
  ],
};
const READY: TaxForms = {
  ...BARE,
  payer: { ...BARE.payer, missing: [], address: ["9 Office Row", "Springfield, ZZ 00000"],
    taxpayer_id: { tin_type: "EIN", tin_last4: "4567", legal_name: "Synthetic PMC LLC", exempt: false, recorded_by: "Staff 1", recorded_at: AT } },
  recipients: [{ ...BARE.recipients[0]!, missing: [],
    taxpayer_id: { tin_type: "SSN", tin_last4: "1234", legal_name: "Synthetic Repairs", exempt: false, recorded_by: "Staff 1", recorded_at: AT } }],
};

function server(views: TaxForms[], refuse = "") {
  let at = 0;
  const fetch = vi.fn(async (_path: string, init?: RequestInit) => {
    if (init?.method === "POST") {
      if (refuse) return new Response(JSON.stringify({ detail: refuse }), { status: 422 });
      at = Math.min(at + 1, views.length - 1);
      return new Response(JSON.stringify({}), { status: 201 });
    }
    return new Response(JSON.stringify(views[at]));
  });
  vi.stubGlobal("fetch", fetch);
  return fetch;
}

describe("TaxFormsPage", () => {
  it("lists who gets a 1099 with what each form needs, and keeps a taxpayer ID", async () => {
    const fetch = server([BARE, READY]);
    render(<TaxFormsPage me={ME} year={2026} />);

    expect((await screen.findByTestId("payer-ready")).textContent).toBe("Needs a taxpayer ID and a mailing address");
    const table = screen.getByRole("table", { name: "1099-NEC: vendors" });
    const row = within(table).getAllByRole("row")[1]!;
    expect(row.textContent).toContain("Synthetic Repairs$2,500.00None on file1 Synthetic Way, Springfield, ZZ 00000Needs a taxpayer ID");
    expect(screen.getByText("No 1099-MISC for 2026")).toBeTruthy();
    fireEvent.click(within(row).getByRole("button", { name: "Taxpayer ID of Synthetic Repairs" }));
    const panel = screen.getByRole("dialog", { name: "Taxpayer ID of Synthetic Repairs" });
    fireEvent.change(within(panel).getByLabelText("Taxpayer ID"), { target: { value: " 000-12-1234 " } });
    fireEvent.click(within(panel).getByRole("button", { name: "Keep it" }));

    expect(await screen.findByText("Kept the taxpayer ID of Synthetic Repairs.")).toBeTruthy();
    expect(screen.queryByRole("dialog")).toBeNull();
    const post = fetch.mock.calls.find(([, init]) => init?.method === "POST");
    expect(JSON.parse(String(post?.[1]?.body))).toEqual({
      about: "vendor", about_id: "v1", tin_type: "SSN", tin: "000-12-1234", legal_name: "Synthetic Repairs", exempt: false,
    });
    await vi.waitFor(() => expect(screen.getByTestId("payer-ready").textContent).toBe("Ready"));
    expect(screen.getByText("Synthetic Repairs, XXX-XX-1234")).toBeTruthy();
    expect(screen.getByRole("link", { name: "Recipient copies (PDF)" }).getAttribute("href")).toBe("/api/tax-forms/2026/copies.pdf?form=nec");
    expect(screen.getByRole("link", { name: "Filing list (CSV)" }).getAttribute("href")).toBe("/api/tax-forms/2026/filing.csv?form=nec");
  });

  it("keeps the company's EIN, and shows a refusal", async () => {
    const fetch = server([BARE], "a taxpayer ID is nine digits");
    render(<TaxFormsPage me={{ ...ME, roles: [] } as Me} year={2026} />);
    fireEvent.click(await screen.findByRole("button", { name: "Set the company's EIN" }));
    const panel = screen.getByRole("dialog", { name: "The company's EIN" });
    expect((within(panel).getByLabelText("Kind of number") as HTMLSelectElement).disabled).toBe(true);
    fireEvent.change(within(panel).getByLabelText("Taxpayer ID"), { target: { value: "12" } });
    fireEvent.click(within(panel).getByRole("button", { name: "Keep it" }));
    expect((await within(panel).findByRole("alert")).textContent).toContain("nine digits");
    const post = fetch.mock.calls.find(([, init]) => init?.method === "POST");
    expect(JSON.parse(String(post?.[1]?.body))).toMatchObject({ about: "company", tin_type: "EIN" });
    expect(JSON.parse(String(post?.[1]?.body))).not.toHaveProperty("about_id");
    // Without admin, no filing list.
    expect(screen.queryByRole("link", { name: "Filing list (CSV)" })).toBeNull();
  });

  it("says when the server can't seal taxpayer IDs", async () => {
    server([{ ...BARE, sealing: false }]);
    render(<TaxFormsPage me={ME} year={2026} />);
    expect(await screen.findByText(/no key to seal taxpayer IDs/)).toBeTruthy();
  });

  it("words a number and what a form needs", () => {
    expect(tinCut({ tin_type: "EIN", tin_last4: "4567" } as never)).toBe("XX-XXX4567");
    expect(tinCut({ tin_type: "ITIN", tin_last4: "1234" } as never)).toBe("XXX-XX-1234");
    expect(needs({ missing: ["mailing address"] } as never)).toBe("Needs a mailing address");
  });
});
