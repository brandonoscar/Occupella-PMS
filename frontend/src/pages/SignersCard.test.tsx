import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { Me } from "../api";
import type { SignerAccount } from "../signers";
import type { Member } from "../staff";
import { SignersCard } from "./SignersCard";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

const ADMIN: Me = {
  kind: "staff", email: "admin@example.com", name: "Admin 1", person_id: "s1", pmc_id: "p1",
  pmc_name: "Synthetic PMC", roles: ["admin", "approve", "disburse"],
};
const MEMBERS: Member[] = [
  { staff_id: "s1", name: "Admin 1", roles: ["admin", "approve", "disburse"] },
  { staff_id: "s2", name: "Clerk 1", roles: ["disburse"] },
];
const ACCOUNTS: SignerAccount[] = [
  {
    bank_account_id: "b1", bank_account: "Operating trust",
    signers: [
      { staff_id: "s1", name: "Admin 1", role: "broker", license_number: "LIC-0001", bond_amount: null,
        bond_expires_on: null, authorized_on: "2026-01-01", standing: "licensed" },
      { staff_id: "s2", name: "Clerk 1", role: "signer", license_number: null, bond_amount: "25000.00",
        bond_expires_on: "2026-01-20", authorized_on: "2026-01-02", standing: "bond runs out in 5 days" },
    ],
  },
  { bank_account_id: "b2", bank_account: "Deposit trust", signers: [] },
];

function server(refuse: string | null = null) {
  const fetch = vi.fn(async (_path: string, init?: RequestInit) => {
    if (init?.method !== "POST") return new Response(JSON.stringify(ACCOUNTS), { status: 200 });
    if (refuse) return new Response(JSON.stringify({ detail: refuse }), { status: 403 });
    return new Response(null, { status: 204 });
  });
  vi.stubGlobal("fetch", fetch);
  return fetch;
}

const posted = (fetch: ReturnType<typeof server>) =>
  fetch.mock.calls
    .filter(([, init]) => init?.method === "POST")
    .map(([path, init]) => [path, JSON.parse(String(init?.body))]);

describe("SignersCard", () => {
  it("lists each account's broker and signers, and says when an account has no broker", async () => {
    server();
    render(<SignersCard me={ADMIN} members={MEMBERS} />);

    const operating = await screen.findByRole("listitem", { name: "Operating trust" });
    expect(operating.textContent).toContain("Admin 1, the broker: license LIC-0001, authorized 01/01/2026");
    expect(operating.textContent).toContain("Clerk 1: bonded for $25,000.00 through 01/20/2026");
    const running = within(operating).getByText("bond runs out in 5 days");
    expect(running.closest("[data-tone]")?.getAttribute("data-tone")).toBe("caution");
    const deposit = screen.getByRole("listitem", { name: "Deposit trust" });
    expect(deposit.textContent).toContain("No broker on file: an admin names the broker.");
  });

  it("names a broker, authorizes a bonded signer and takes a signer away", async () => {
    const fetch = server();
    render(<SignersCard me={ADMIN} members={MEMBERS} />);
    await screen.findByRole("listitem", { name: "Deposit trust" });

    fireEvent.change(screen.getByLabelText("Trust bank account"), { target: { value: "b2" } });
    fireEvent.change(screen.getByLabelText("License number"), { target: { value: "LIC-0001" } });
    fireEvent.change(screen.getByLabelText("Authorized in writing on"), { target: { value: "2026-01-05" } });
    fireEvent.click(screen.getByRole("button", { name: "Record" }));
    expect(await screen.findByText("Admin 1 is the broker of Deposit trust.")).toBeTruthy();

    fireEvent.change(screen.getByLabelText("Staff member"), { target: { value: "s2" } });
    fireEvent.change(screen.getByLabelText("As"), { target: { value: "signer" } });
    fireEvent.change(screen.getByLabelText("Or bonded for"), { target: { value: "25000.00" } });
    fireEvent.change(screen.getByLabelText("Bond runs out"), { target: { value: "2027-01-01" } });
    fireEvent.click(screen.getByRole("button", { name: "Record" }));
    expect(await screen.findByText("Clerk 1 signs Deposit trust.")).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: "Take away Clerk 1" }));
    expect(await screen.findByText("Clerk 1 no longer signs Operating trust.")).toBeTruthy();

    const [broker, signer, away] = posted(fetch) as [string, Record<string, unknown>][];
    expect(broker?.[1]).toMatchObject({
      bank_account_id: "b2", staff_id: "s1", role: "broker", granted: true, license_number: "LIC-0001",
      bond_amount: null, authorized_on: "2026-01-05",
    });
    expect(signer?.[1]).toMatchObject({
      bank_account_id: "b2", staff_id: "s2", role: "signer", license_number: null, bond_amount: "25000.00",
      bond_expires_on: "2027-01-01",
    });
    expect(away?.[1]).toMatchObject({ bank_account_id: "b1", staff_id: "s2", role: "signer", granted: false });
  });

  it("offers a staff member who isn't an admin only to add a signer, and shows a refusal", async () => {
    server("only this trust bank account's broker authorizes its signers");
    render(<SignersCard me={{ ...ADMIN, roles: ["disburse"] }} members={MEMBERS} />);
    await screen.findByRole("listitem", { name: "Operating trust" });

    const as = screen.getByLabelText("As") as HTMLSelectElement;
    expect(Array.from(as.options).map((o) => o.value)).toEqual(["signer"]);
    fireEvent.change(screen.getByLabelText("License number"), { target: { value: "LIC-0002" } });
    fireEvent.click(screen.getByRole("button", { name: "Record" }));

    expect(await screen.findByText("only this trust bank account's broker authorizes its signers")).toBeTruthy();
  });
});
