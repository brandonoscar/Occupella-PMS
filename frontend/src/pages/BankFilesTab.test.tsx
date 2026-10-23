import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { AchSettings, BankFile, Payout } from "./BankFilesTab";
import { BankFilesTab, nextWeekday, settingsLine } from "./BankFilesTab";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

const SETTINGS: AchSettings = {
  bank_routing: "001000009",
  bank_name: "Demo Trust Bank",
  origin: "1000000001",
  company_name: "Demo Property Co",
  company_id: "1000000001",
  recorded_by: "Staff 1",
  recorded_at: "2026-10-23T01:00:00Z",
};

const WAITING: Payout[] = [
  {
    transfer_id: "t1", paid_on: "2026-10-22", payee: "owner", payee_id: "o1", name: "Demo Owner A",
    what: "Owner draw, Elm Court", amount: "125.50", bank_account: "Checking ••••6789",
  },
  {
    transfer_id: "t2", paid_on: "2026-10-22", payee: "vendor", payee_id: "v1", name: "Demo Roofing Co",
    what: "Bill ROOF-17", amount: "1000.00", bank_account: "Checking ••••5501",
  },
  {
    transfer_id: "t3", paid_on: "2026-10-22", payee: "vendor", payee_id: "v2", name: "Demo Lawn Co",
    what: "Bill LAWN-3", amount: "80.00", bank_account: null,
  },
];

const FILE: BankFile = {
  id: "f1", made_at: "2026-10-23T02:00:00Z", made_by: "Staff 1", modifier: "A", effective_on: "2026-10-26",
  entries: 2, total: "1125.50", voided: null,
  items: [
    { name: "Demo Owner A", what: "Owner draw, Elm Court", amount: "125.50", bank_account: "Checking ••••6789" },
    { name: "Demo Roofing Co", what: "Bill ROOF-17", amount: "1000.00", bank_account: "Checking ••••5501" },
  ],
};

function server(start: { settings: AchSettings | null; waiting: Payout[]; files: BankFile[] }) {
  let now = start;
  const fetch = vi.fn(async (path: string, init?: RequestInit) => {
    if (init?.method !== "POST") return new Response(JSON.stringify(now), { status: 200 });
    const body = JSON.parse(String(init.body)) as Record<string, unknown>;
    if (path.endsWith("/settings")) {
      now = { ...now, settings: { ...SETTINGS, ...body } as AchSettings };
      return new Response(JSON.stringify(now.settings), { status: 201 });
    }
    if (path.endsWith("/void")) {
      now = { ...now, files: now.files.map((f) => ({ ...f, voided: { reason: String(body.reason), by: "Staff 1", at: "" } })) };
      return new Response(null, { status: 204 });
    }
    now = { ...now, waiting: now.waiting.filter((p) => !(body.transfer_ids as string[]).includes(p.transfer_id)), files: [FILE] };
    return new Response(JSON.stringify(FILE), { status: 201 });
  });
  vi.stubGlobal("fetch", fetch);
  return fetch;
}

const posts = (fetch: ReturnType<typeof server>) =>
  fetch.mock.calls.filter(([, init]) => init?.method === "POST").map(([path, init]) => [path, JSON.parse(String(init?.body))]);

describe("BankFilesTab", () => {
  it("says the next weekday and the settings in words", () => {
    expect(nextWeekday(new Date("2026-10-23T12:00:00Z"))).toBe("2026-10-26"); // a Friday: Monday
    expect(nextWeekday(new Date("2026-10-26T12:00:00Z"))).toBe("2026-10-27");
    expect(settingsLine(SETTINGS)).toBe(
      "Demo Trust Bank, routing 001000009; Demo Property Co, company ID 1000000001, origin 1000000001",
    );
  });

  it("makes a file of the payouts picked after a question naming the total", async () => {
    const fetch = server({ settings: SETTINGS, waiting: WAITING, files: [] });
    render(<BankFilesTab bankId="b1" />);

    const list = await screen.findByRole("list", { name: "EFT payouts waiting" });
    expect(within(list).getAllByRole("listitem").map((li) => li.textContent)).toEqual([
      "10/22/2026 Owner draw, Elm Court, Demo Owner A: $125.50Checking ••••6789",
      "10/22/2026 Bill ROOF-17, Demo Roofing Co: $1,000.00Checking ••••5501",
      "10/22/2026 Bill LAWN-3, Demo Lawn Co: $80.00No bank account on file: add one",
    ]);
    // A payee with no bank account can't be picked, and their page is a click away.
    expect((within(list).getAllByRole("checkbox")[2] as HTMLInputElement).disabled).toBe(true);
    expect(within(list).getByRole("link", { name: "No bank account on file: add one" }).getAttribute("href")).toBe("/vendors/v2");

    fireEvent.click(within(list).getAllByRole("checkbox")[0]!);
    fireEvent.click(within(list).getAllByRole("checkbox")[1]!);
    fireEvent.change(screen.getByLabelText("Effective day"), { target: { value: "2026-10-26" } });
    fireEvent.click(screen.getByRole("button", { name: "Make bank file of $1,125.50" }));
    const asked = screen.getByRole("alertdialog");
    expect(asked.textContent).toContain("Make a bank file paying $1,125.50 to 2 payees, effective 10/26/2026?");
    fireEvent.click(within(asked).getByRole("button", { name: "Make bank file" }));

    expect(await screen.findByText("Bank file A made: 2 payouts, $1,125.50.")).toBeTruthy();
    expect(posts(fetch)).toEqual([["/api/bank-files/b1", { effective_on: "2026-10-26", transfer_ids: ["t1", "t2"] }]]);
    const files = await screen.findByRole("table", { name: "Bank files" });
    expect(within(files).getByRole("link", { name: "Download" }).getAttribute("href")).toBe("/api/bank-files/files/f1.ach");
    expect(within(files).getByRole("row", { name: "File A of 10/23/2026" }).textContent).toContain("$1,125.50");
  });

  it("keeps the settings from the bank and voids a file with why", async () => {
    const fetch = server({ settings: null, waiting: [], files: [FILE] });
    render(<BankFilesTab bankId="b1" />);

    const group = await screen.findByRole("group", { name: "ACH settings" });
    expect(group.textContent).toContain("No ACH settings yet.");
    fireEvent.click(within(group).getByRole("button", { name: "Add ACH settings" }));
    const panel = screen.getByRole("dialog", { name: "ACH settings" });
    fireEvent.change(within(panel).getByLabelText("Bank's routing number"), { target: { value: "001000009" } });
    fireEvent.change(within(panel).getByLabelText("Bank's name"), { target: { value: "Demo Trust Bank" } });
    fireEvent.change(within(panel).getByLabelText("Immediate origin"), { target: { value: "1000000001" } });
    fireEvent.change(within(panel).getByLabelText("Company name"), { target: { value: "Demo Property Co" } });
    fireEvent.change(within(panel).getByLabelText("Company ID"), { target: { value: "1000000001" } });
    fireEvent.click(within(panel).getByRole("button", { name: "Save ACH settings" }));
    expect(await screen.findByText("ACH settings saved.")).toBeTruthy();
    expect((await screen.findByTestId("ach-settings")).textContent).toBe(settingsLine(SETTINGS));

    fireEvent.click(screen.getByRole("button", { name: "Actions for file A" }));
    fireEvent.click(await screen.findByRole("menuitem", { name: "Void" }));
    const voiding = screen.getByRole("dialog", { name: "Void bank file A" });
    expect(voiding.textContent).toContain("its 2 payouts wait for another file again");
    fireEvent.change(within(voiding).getByLabelText("Why"), { target: { value: "The bank refused it" } });
    fireEvent.click(within(voiding).getByRole("button", { name: "Void bank file" }));
    expect(await screen.findByText("Bank file A is void; its payouts wait for another.")).toBeTruthy();
    expect(posts(fetch).at(-1)).toEqual(["/api/bank-files/files/f1/void", { reason: "The bank refused it" }]);
    expect((await screen.findByText("Void: The bank refused it")).textContent).toBeTruthy();
    expect(screen.queryByRole("link", { name: "Download" })).toBeNull();
  });

  it("says so when nothing waits and no file was made", async () => {
    server({ settings: SETTINGS, waiting: [], files: [] });
    render(<BankFilesTab bankId="b1" />);
    expect(await screen.findByText("No EFT payout of the last 30 days waits for a bank file.")).toBeTruthy();
    expect(screen.getByText("No bank file made yet.")).toBeTruthy();
  });
});
