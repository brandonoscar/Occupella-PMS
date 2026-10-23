import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { Owing, Run } from "../balanceNotices";
import { paperStates, sentLine } from "../balanceNotices";
import { BalanceNoticesPanel } from "./BalanceNoticesPanel";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

const OH = { state: "OH", email_may_count: false, words: "In OH the legal notice for nonpayment goes by mail, posting or hand." };
const FL = { state: "FL", email_may_count: true, words: "In FL email may serve the legal notice only with written consent (Fla. Stat. 83.505)." };
const OWING: Owing[] = [
  { lease_id: "l1", property: "Property 1", unit: "1A", owed: "1435.00", delivery: OH,
    tenants: [{ id: "t1", name: "Avery Sample", email: null }, { id: "t2", name: "Reese Sample", email: "reese@example.com" }] },
  { lease_id: "l2", property: "Property 2", unit: "2A", owed: "100.00", delivery: FL,
    tenants: [{ id: "t3", name: "Jordan Sample", email: "jordan@example.com" }] },
];
const TEMPLATES = {
  templates: [
    { id: "n1", version_id: "v1", name: "Balance reminder", body: "Hi", used_for: "notice", saved_at: "2026-10-23T12:00:00Z", saved_by: null },
    { id: "x1", version_id: "v2", name: "Lease", body: "Lease", used_for: "lease", saved_at: "2026-10-23T12:00:00Z", saved_by: null },
  ],
  fields: [], lease_fields: [],
};
const RUN: Run = {
  id: "r1",
  sent: [{ lease_id: "l1", notice_id: "n9", property: "Property 1", unit: "1A", owed: "1435.00", delivery: OH,
    emailed: [{ id: "t2", name: "Reese Sample", email: "reese@example.com" }], without_email: ["Avery Sample"] }],
};

function server(owing: Owing[] = OWING, templates = TEMPLATES, refuse = "", texting = false) {
  const fetch = vi.fn(async (path: string, init?: RequestInit) => {
    if (init?.method === "POST") {
      if (refuse) return new Response(JSON.stringify({ detail: refuse }), { status: 422 });
      const asked = JSON.parse(String(init.body));
      const run = asked.text ? { ...RUN, sent: RUN.sent.map((s) => ({ ...s, texted: ["Reese Sample"] })) } : RUN;
      return new Response(JSON.stringify(run), { status: 201 });
    }
    if (path === "/api/texts/settings") {
      return new Response(JSON.stringify({ rent_reminder_days: null, late_notices: false, visits: false, sending: texting }));
    }
    if (path.startsWith("/api/balance-notices/owing")) return new Response(JSON.stringify(owing));
    return new Response(JSON.stringify(templates));
  });
  vi.stubGlobal("fetch", fetch);
  return fetch;
}

describe("BalanceNoticesPanel", () => {
  it("picks every lease that owes, sends the picked ones and offers their PDF", async () => {
    const fetch = server();
    const sent = vi.fn();
    render(<BalanceNoticesPanel scope={{ group: "g1", property: "" }} onClose={vi.fn()} onSent={sent} />);

    const table = await screen.findByRole("table", { name: "Leases that owe" });
    expect(within(table).getAllByRole("row")[1]!.textContent).toBe("Property 1, 1AAvery Sample (no email on file), Reese Sample$1,435.00");
    expect(screen.getByTestId("paper-states").textContent).toMatch(/^In OH the legal notice for nonpayment must be delivered by mail, posting or hand/);
    expect(within(screen.getByRole("list", { name: "How the legal notice goes" })).getByRole("listitem").textContent).toContain("83.505");
    // Only notice templates are offered.
    expect(within(screen.getByLabelText("Notice template")).getAllByRole("option").map((o) => o.textContent)).toEqual(["Balance reminder"]);
    fireEvent.click(screen.getByLabelText("Send to Property 2, 2A"));
    expect(screen.queryByRole("list", { name: "How the legal notice goes" })).toBeNull();
    fireEvent.change(screen.getByLabelText("Dated"), { target: { value: "2026-10-24" } });
    fireEvent.click(screen.getByRole("button", { name: "Send 1 balance notice" }));

    expect((await screen.findByTestId("balance-notices-sent")).textContent).toBe(
      "Made a balance notice for 1 lease: emailed 1 tenant; 1 tenant has no email on file.",
    );
    expect(screen.getByRole("list", { name: "No email on file" }).textContent).toBe("Property 1, 1A: Avery Sample");
    expect(screen.getByRole("link", { name: "Print the notices (PDF)" }).getAttribute("href")).toBe("/api/balance-notices/runs/r1/pdf");
    expect(sent).toHaveBeenCalledWith("Made a balance notice for 1 lease: emailed 1 tenant; 1 tenant has no email on file.");
    expect(fetch.mock.calls.map(([path]) => path)).toContain("/api/balance-notices/owing?group=g1");
    const post = fetch.mock.calls.find(([, init]) => init?.method === "POST");
    expect(JSON.parse(String(post?.[1]?.body))).toEqual({ template_id: "n1", dated_on: "2026-10-24", lease_ids: ["l1"], text: false });
    // No text is offered while the server sends none.
    expect(screen.queryByLabelText(/Also text a reminder/)).toBeNull();
  });

  it("texts a reminder too, to tenants who agreed, while the server sends texts", async () => {
    const fetch = server(OWING, TEMPLATES, "", true);
    render(<BalanceNoticesPanel scope={{ group: "", property: "" }} onClose={vi.fn()} />);
    fireEvent.click(await screen.findByLabelText(/Also text a reminder to each tenant who agreed to texts/));
    fireEvent.click(screen.getByLabelText("Send to Property 2, 2A"));
    fireEvent.click(screen.getByRole("button", { name: "Send 1 balance notice" }));
    expect((await screen.findByTestId("balance-notices-sent")).textContent).toBe(
      "Made a balance notice for 1 lease: emailed 1 tenant; 1 tenant has no email on file; texted 1 tenant a reminder.",
    );
    const post = fetch.mock.calls.find(([, init]) => init?.method === "POST");
    expect(JSON.parse(String(post?.[1]?.body)).text).toBe(true);
  });

  it("picks none and all, and shows a refusal", async () => {
    server(OWING, TEMPLATES, "Pay or quit reads like the legal notice for nonpayment (quit)");
    render(<BalanceNoticesPanel scope={{ group: "", property: "" }} onClose={vi.fn()} />);
    fireEvent.click(await screen.findByRole("button", { name: "Pick none" }));
    expect((screen.getByRole("button", { name: "Send 0 balance notices" }) as HTMLButtonElement).disabled).toBe(true);
    fireEvent.click(screen.getByRole("button", { name: "Pick all" }));
    fireEvent.click(screen.getByRole("button", { name: "Send 2 balance notices" }));
    expect((await screen.findByRole("alert")).textContent).toContain("reads like the legal notice");
  });

  it("says when no lease owes, or no notice template exists", async () => {
    server([]);
    render(<BalanceNoticesPanel scope={{ group: "", property: "" }} onClose={vi.fn()} />);
    expect(await screen.findByText("No lease owes anything now.")).toBeTruthy();
    cleanup();
    server(OWING, { ...TEMPLATES, templates: [] });
    render(<BalanceNoticesPanel scope={{ group: "", property: "" }} onClose={vi.fn()} />);
    expect((await screen.findByRole("link", { name: "Settings, Notice templates" })).getAttribute("href")).toBe("/settings/notices");
  });

  it("words what a send did and which states want paper", () => {
    expect(sentLine({ id: "r", sent: [] })).toBe("Made balance notices for 0 leases: emailed 0 tenants.");
    expect(paperStates([{ delivery: OH }, { delivery: FL }, { delivery: { ...OH } }, { delivery: { state: null, email_may_count: null, words: "" } }])).toEqual(["OH"]);
  });
});
