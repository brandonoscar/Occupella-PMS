import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { Me } from "../api";
import type { Cutover } from "../switching";
import { SwitchPage } from "./SwitchPage";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

const ME: Me = {
  kind: "staff",
  email: "staff@example.com",
  name: "Staff 1",
  person_id: "s1",
  pmc_id: "p1",
  pmc_name: "Synthetic PMC",
};

const TIE_OUT = [
  { line: 1, item: "PMC", detail: "Synthetic PMC", amount: null },
  { line: 2, item: "trust bank account", detail: "Operating (operating)", amount: null },
  { line: 3, item: "cutover", detail: "2026-01-01 (opening balances at the start of the day, UTC)", amount: null },
  { line: 4, item: "old system's book cash", detail: null, amount: "1000.00" },
  { line: 5, item: "opening balance", detail: "owner_property: Owner 1 / Property 1", amount: "1000.00" },
  { line: 6, item: "outstanding check", detail: "2025-12-30 Check (1041)", amount: "-200.00" },
  { line: 7, item: "bank balance to expect at the cutover", detail: "book cash less the items the bank had not cleared", amount: "1200.00" },
  { line: 8, item: "signed off by", detail: "not signed off yet", amount: null },
];

const CUTOVERS: Cutover[] = [
  { bank_account_id: "b1", name: "Operating", kind: "operating", can_open: false, opened_on: "2026-01-01", signed_off: false, tie_out: TIE_OUT },
  { bank_account_id: "b2", name: "Deposits", kind: "security_deposit", can_open: true, opened_on: null, signed_off: false, tie_out: [] },
  { bank_account_id: "b3", name: "Old operating", kind: "operating", can_open: false, opened_on: null, signed_off: false, tie_out: [] },
  {
    bank_account_id: "b4", name: "Signed", kind: "operating", can_open: false, opened_on: "2026-01-01", signed_off: true,
    tie_out: [...TIE_OUT.slice(0, 7), { line: 8, item: "signed off by", detail: "Staff 1 at 2026-01-02 10:30:00 UTC", amount: null }],
  },
];

function server(answers: Record<string, unknown> = {}, refuse: Record<string, string> = {}) {
  const fetch = vi.fn(async (path: string, init?: RequestInit) => {
    if (init?.method !== "POST") return new Response(JSON.stringify(CUTOVERS), { status: 200 });
    if (path in refuse) return new Response(JSON.stringify({ detail: refuse[path] }), { status: 422 });
    return new Response(JSON.stringify(answers[path] ?? {}), { status: 201 });
  });
  vi.stubGlobal("fetch", fetch);
  return fetch;
}

const posted = (fetch: ReturnType<typeof server>) =>
  fetch.mock.calls
    .filter(([, init]) => init?.method === "POST")
    .map(([path, init]) => [path, JSON.parse(String(init?.body))]);

const csvFile = (text: string) => new File([text], "file.csv", { type: "text/csv" });

describe("SwitchPage", () => {
  it("imports a roster file of the kind chosen and says what it added", async () => {
    const fetch = server({ "/api/switching/import": { added: 2, kept: 1 } });
    render(<SwitchPage me={ME} />);
    await screen.findByText("Cutover: Operating");
    const importing = screen.getByRole("button", { name: "Import" }) as HTMLButtonElement;
    expect(importing.disabled).toBe(true);

    fireEvent.change(screen.getByLabelText("File of"), { target: { value: "leases" } });
    expect(screen.getByText(/Columns: Property, Unit, Tenants/)).toBeTruthy();
    fireEvent.change(screen.getByLabelText("CSV file"), { target: { files: [csvFile("Property,Unit\n")] } });
    await vi.waitFor(() => expect(importing.disabled).toBe(false));
    fireEvent.click(importing);

    expect(await screen.findByText("2 added; 1 already here.")).toBeTruthy();
    expect(posted(fetch)).toEqual([["/api/switching/import", { kind: "leases", content: "Property,Unit\n" }]]);
  });

  it("carries over what the old system paid each vendor before the cutover", async () => {
    const fetch = server({ "/api/switching/vendor-payments": { added: 2, kept: 0 } });
    render(<SwitchPage me={ME} />);
    await screen.findByText("Cutover: Operating");
    const carrying = screen.getByRole("button", { name: "Carry over" }) as HTMLButtonElement;
    expect(carrying.disabled).toBe(true);
    expect(screen.getByText(/Columns: Vendor, Paid/)).toBeTruthy();

    fireEvent.change(screen.getByLabelText("Cutover day"), { target: { value: "2026-03-01" } });
    fireEvent.change(screen.getByLabelText("Vendor payments (CSV)"), {
      target: { files: [csvFile("Vendor,Paid\nVendor A,120.00\n")] },
    });
    await vi.waitFor(() => expect(carrying.disabled).toBe(false));
    fireEvent.click(carrying);

    expect(await screen.findByText("2 added.")).toBeTruthy();
    expect(posted(fetch)).toEqual([
      ["/api/switching/vendor-payments", { paid_before: "2026-03-01", content: "Vendor,Paid\nVendor A,120.00\n" }],
    ]);
  });

  it("opens an account with its old balances", async () => {
    const fetch = server({ "/api/switching/opening-balances": { accounts: 3 } });
    render(<SwitchPage me={ME} />);
    await screen.findByText("Cutover: Deposits");

    fireEvent.change(screen.getByLabelText("Cutover date"), { target: { value: "2026-01-01" } });
    fireEvent.change(screen.getByLabelText("Old system's book cash"), { target: { value: "4500.00" } });
    fireEvent.change(screen.getByLabelText("Opening balances (CSV)"), { target: { files: [csvFile("Kind,Amount\n")] } });
    const opening = screen.getByRole("button", { name: "Open with balances" }) as HTMLButtonElement;
    await vi.waitFor(() => expect(opening.disabled).toBe(false));
    fireEvent.click(opening);

    expect(await screen.findByText("Opened with 3 balances.")).toBeTruthy();
    expect(posted(fetch)).toEqual([
      ["/api/switching/opening-balances", { bank_account_id: "b2", opened_on: "2026-01-01", book_cash: "4500.00", content: "Kind,Amount\n" }],
    ]);
    expect(screen.getByText(/has postings or a reconciliation already/)).toBeTruthy();
  });

  it("shows the tie-out, adds items carried over and signs it off", async () => {
    const fetch = server({ "/api/switching/items": { added: 1, kept: 0 } });
    render(<SwitchPage me={ME} />);
    const tieOut = await screen.findByLabelText("Tie-out, Operating");

    expect(tieOut.textContent).toContain("outstanding check: 2025-12-30 Check (1041)-$200.00");
    expect(tieOut.textContent).toContain("signed off bynot signed off yet");
    expect(screen.getByText(/The bank's statement should open at \$1,200.00/)).toBeTruthy();
    fireEvent.change(screen.getByLabelText("Checks and deposits not cleared (CSV)"), {
      target: { files: [csvFile("Date,Amount,Description\n")] },
    });
    const adding = screen.getByRole("button", { name: "Add items" }) as HTMLButtonElement;
    await vi.waitFor(() => expect(adding.disabled).toBe(false));
    fireEvent.click(adding);
    expect(await screen.findByText("1 item carried over.")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Sign off the tie-out" }));
    expect(await screen.findByText("Signed off. The items carried over are frozen.")).toBeTruthy();

    expect(posted(fetch)).toEqual([
      ["/api/switching/items", { bank_account_id: "b1", content: "Date,Amount,Description\n" }],
      ["/api/switching/sign-off", { bank_account_id: "b1" }],
    ]);
    expect(screen.getAllByRole("link", { name: "Tie-out (PDF)" })[0]?.getAttribute("href")).toBe(
      "/api/reports/trust_report_cutover?arg=b1&format=pdf",
    );
    expect(screen.getByLabelText("Tie-out, Signed").textContent).toContain("Staff 1 at 2026-01-02");
    expect(screen.getAllByRole("button", { name: "Sign off the tie-out" })).toHaveLength(1);
  });

  it("shows a refusal, and when there is no trust bank account or the page can't be read", async () => {
    server({}, { "/api/switching/import": "line 2: no owner 'Owner Z'; import the owners first" });
    render(<SwitchPage me={ME} />);
    await screen.findByText("Cutover: Operating");
    fireEvent.change(screen.getByLabelText("CSV file"), { target: { files: [csvFile("Name\n")] } });
    const importing = screen.getByRole("button", { name: "Import" }) as HTMLButtonElement;
    await vi.waitFor(() => expect(importing.disabled).toBe(false));
    fireEvent.click(importing);
    expect(await screen.findByText("line 2: no owner 'Owner Z'; import the owners first")).toBeTruthy();
    fireEvent.change(screen.getByLabelText("CSV file"), { target: { files: [] } });
    await vi.waitFor(() => expect(importing.disabled).toBe(true));
    cleanup();

    vi.stubGlobal("fetch", vi.fn(async () => new Response("[]", { status: 200 })));
    render(<SwitchPage me={ME} />);
    expect(await screen.findByText("Add a trust bank account in Setup first.")).toBeTruthy();
    cleanup();

    vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({ detail: "sign in first" }), { status: 401 })));
    render(<SwitchPage me={ME} />);
    expect((await screen.findAllByRole("alert"))[0]?.textContent).toBe("sign in first");
  });
});
