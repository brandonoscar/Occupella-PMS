import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { EmailSettings } from "./AutomaticEmailsCard";
import { AutomaticEmailsCard, emailSettingsLines, ordinal } from "./AutomaticEmailsCard";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

const OFF: EmailSettings = {
  rent_reminder_days: null, late_notices: false, owner_statement_day: null,
  work_order_notices: false, adverse_action_notices: false, sending: true,
};

function server(settings: EmailSettings, refuse?: string) {
  let now = settings;
  const fetch = vi.fn(async (_path: string, init?: RequestInit) => {
    if (init?.method === "POST") {
      if (refuse) return new Response(JSON.stringify({ detail: refuse }), { status: 403 });
      now = { ...JSON.parse(String(init.body)), sending: now.sending };
    }
    return new Response(JSON.stringify(now), { status: 200 });
  });
  vi.stubGlobal("fetch", fetch);
  return fetch;
}

describe("emailSettingsLines", () => {
  it("says each automatic email in words", () => {
    expect(emailSettingsLines(OFF).map(([, how]) => how)).toEqual(["Off", "Off", "Off", "Off", "Off"]);
    expect(emailSettingsLines({
      ...OFF, rent_reminder_days: 1, late_notices: true, owner_statement_day: 22, work_order_notices: true,
    })).toEqual([
      ["Rent reminders to tenants", "1 day before rent is due"],
      ["Late fee notices to tenants", "On"],
      ["Monthly statements to owners", "Last month's, from the 22nd of each month"],
      ["Work orders to vendors", "On"],
      ["Adverse action notices to applicants", "Off"],
    ]);
  });

  it("writes a day of the month as people say it", () => {
    expect([1, 2, 3, 4, 11, 12, 13, 21, 23, 28].map(ordinal)).toEqual([
      "1st", "2nd", "3rd", "4th", "11th", "12th", "13th", "21st", "23rd", "28th",
    ]);
  });
});

describe("AutomaticEmailsCard", () => {
  it("turns emails on in a side panel and shows what it saved", async () => {
    const fetch = server(OFF);
    render(<AutomaticEmailsCard />);
    expect((await screen.findByLabelText("Automatic emails")).textContent).toContain("Rent reminders to tenantsOff");
    fireEvent.click(screen.getByRole("button", { name: "Edit automatic emails" }));
    const panel = screen.getByRole("dialog", { name: "Automatic emails" });
    fireEvent.click(within(panel).getByLabelText("Rent reminders to tenants"));
    fireEvent.change(within(panel).getByLabelText("Days before rent is due"), { target: { value: "3" } });
    fireEvent.click(within(panel).getByLabelText("Monthly statements to owners"));
    fireEvent.change(within(panel).getByLabelText("Day of the month they go"), { target: { value: "10" } });
    fireEvent.click(within(panel).getByLabelText("Adverse action notices to applicants"));
    fireEvent.click(within(panel).getByRole("button", { name: "Save" }));

    expect(await screen.findByText("Saved the automatic emails.")).toBeTruthy();
    expect(screen.queryByRole("dialog")).toBeNull();
    const [, init] = fetch.mock.calls.find(([, i]) => i?.method === "POST") ?? [];
    expect(JSON.parse(String(init?.body))).toEqual({
      rent_reminder_days: 3, late_notices: false, owner_statement_day: 10,
      work_order_notices: false, adverse_action_notices: true,
    });
    expect((await screen.findByText("3 days before rent is due"))).toBeTruthy();
    expect(screen.getByText("Last month's, from the 10th of each month")).toBeTruthy();
  });

  it("says when the server sends no email, and why a save was refused", async () => {
    server({ ...OFF, sending: false }, "Clerk 1 may not change email to tenants, owners and vendors");
    render(<AutomaticEmailsCard />);
    expect(await screen.findByText("This server sends no email yet: none of these is written until it does.")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Edit automatic emails" }));
    const panel = screen.getByRole("dialog", { name: "Automatic emails" });
    fireEvent.click(within(panel).getByLabelText("Late fee notices to tenants"));
    fireEvent.click(within(panel).getByRole("button", { name: "Save" }));
    expect((await within(panel).findByRole("alert")).textContent).toContain("may not change email");
  });

  it("says when the settings can't be read", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({ detail: "sign in first" }), { status: 401 })));
    render(<AutomaticEmailsCard />);
    expect((await screen.findByRole("alert")).textContent).toBe("sign in first");
  });
});
