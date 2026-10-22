import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { holdBack } from "../holdBack";
import type { EmailLine } from "./EmailsCard";
import { EmailsCard, WriteEmail } from "./EmailsCard";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

const EMAILS: EmailLine[] = [
  {
    id: "e3", kind: "message", to: "t@example.com", subject: "Water shutoff", body: "Off Tuesday.\n\nStaff 1\nDemo PM\n",
    at: "2026-10-16T15:00:00Z", by: "Staff 1", status: "waiting", error: null,
  },
  {
    id: "e2", kind: "invite", to: "t@example.com", subject: "Demo PM invites you to sign in",
    body: "Set your password:\n(the sign-in link, sent only in the email)\n", at: "2026-10-12T15:00:00Z",
    by: null, status: "failed", error: "its sign-in link is held only until it is sent, and it was not sent: send a new one",
  },
  {
    id: "e1", kind: "password_reset", to: "t@example.com", subject: "Set a new password for Demo PM", body: "x",
    at: "2026-10-01T15:00:00Z", by: null, status: "sent", error: null,
  },
];

function server(emails: unknown = EMAILS, refuse?: string) {
  const fetch = vi.fn(async (_path: string, init?: RequestInit) => {
    if (init?.method === "POST") {
      return refuse
        ? new Response(JSON.stringify({ detail: refuse }), { status: 422 })
        : new Response(JSON.stringify({ id: "e4", name: "Tenant 1", to: "t@example.com" }), { status: 201 });
    }
    return new Response(JSON.stringify(emails), { status: 200 });
  });
  vi.stubGlobal("fetch", fetch);
  return fetch;
}

describe("EmailsCard", () => {
  it("lists every email to a person newest first, what it was, to whom, by whom and whether it went", async () => {
    const fetch = server();
    render(<EmailsCard about="tenant" aboutId="t1" name="Tenant 1" />);
    const list = await screen.findByRole("list", { name: "Email, Tenant 1" });
    const items = within(list).getAllByRole("listitem");
    expect(items.map((item) => item.querySelector("p")?.textContent)).toEqual([
      "Water shutoffWaiting to send",
      "Demo PM invites you to sign inNot sent",
      "Set a new password for Demo PMSent",
    ]);
    expect(items[0]?.textContent).toContain("Email to t@example.com · Staff 1 · 10/16/2026");
    expect(items[1]?.textContent).toContain("Portal invite to t@example.com · Sent by the PMS");
    expect(items[1]?.textContent).toContain("send a new one");
    expect(items[2]?.textContent).toContain("Password reset");
    expect(fetch.mock.calls[0]?.[0]).toBe("/api/emails?about=tenant&about_id=t1");
  });

  it("shows the email of the person opened now, even when an earlier person's answer comes back last", async () => {
    const other: EmailLine[] = [{ ...EMAILS[0]!, id: "e9", to: "t2@example.com", subject: "Gate code" }];
    vi.stubGlobal("fetch", vi.fn(async (path: string) =>
      new Response(JSON.stringify(path.endsWith("about_id=t1") ? EMAILS : other))));
    const release = holdBack((path) => path.endsWith("about_id=t1"));
    const { rerender } = render(<EmailsCard about="tenant" aboutId="t1" name="Tenant 1" />);
    rerender(<EmailsCard about="tenant" aboutId="t2" name="Tenant 2" />);
    await screen.findByText("Gate code");
    await release();

    expect(screen.getByText("Gate code")).toBeTruthy();
    expect(screen.queryByText("Water shutoff")).toBeNull();
  });

  it("writes an email in a side panel and says where it went", async () => {
    const fetch = server([]);
    render(<EmailsCard about="vendor" aboutId="v1" name="Vendor 1" emailOn />);
    expect(await screen.findByText("No email to Vendor 1 yet.")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Write email" }));
    const panel = screen.getByRole("dialog", { name: "Email Vendor 1" });
    fireEvent.change(within(panel).getByLabelText("Subject"), { target: { value: " Gate code " } });
    fireEvent.change(within(panel).getByLabelText("Message"), { target: { value: "It is 0000." } });
    fireEvent.click(within(panel).getByRole("button", { name: "Send email" }));
    expect(await screen.findByText("Email sent to t@example.com.")).toBeTruthy();
    expect(screen.queryByRole("dialog")).toBeNull();
    const [, init] = fetch.mock.calls.find(([, i]) => i?.method === "POST") ?? [];
    expect(JSON.parse(String(init?.body))).toEqual({
      about: "vendor", about_id: "v1", subject: "Gate code", body: "It is 0000.",
    });
  });

  it("keeps the panel open with why an email was refused", async () => {
    server([], "Tenant 1 has no email on file: add one on their page first");
    render(<EmailsCard about="tenant" aboutId="t1" name="Tenant 1" />);
    await screen.findByText("No email to Tenant 1 yet.");
    fireEvent.click(screen.getByRole("button", { name: "Write email" }));
    const panel = screen.getByRole("dialog", { name: "Email Tenant 1" });
    fireEvent.change(within(panel).getByLabelText("Subject"), { target: { value: "Hi" } });
    fireEvent.change(within(panel).getByLabelText("Message"), { target: { value: "Hi" } });
    fireEvent.click(within(panel).getByRole("button", { name: "Send email" }));
    expect((await within(panel).findByRole("alert")).textContent).toContain("no email on file");
  });

  it("says when the server sends no email yet, and that what is written waits", async () => {
    server([]);
    render(<EmailsCard about="owner" aboutId="o1" name="Owner 1" emailOn={false} />);
    expect(await screen.findByText("This server sends no email yet: what you write waits until it does.")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Write email" }));
    const panel = screen.getByRole("dialog", { name: "Email Owner 1" });
    fireEvent.change(within(panel).getByLabelText("Subject"), { target: { value: "Hi" } });
    fireEvent.change(within(panel).getByLabelText("Message"), { target: { value: "Hi" } });
    fireEvent.click(within(panel).getByRole("button", { name: "Send email" }));
    expect(await screen.findByText("Email to t@example.com waits to send.")).toBeTruthy();
  });

  it("names each email the PMS writes by itself", async () => {
    const at = "2026-10-27T06:00:00Z";
    const line = (id: string, kind: EmailLine["kind"], subject: string): EmailLine =>
      ({ id, kind, to: "t@example.com", subject, body: "x", at, by: null, status: "sent", error: null });
    server([
      line("a", "rent_reminder", "Rent reminder: due 11/01/2026"),
      line("b", "late_notice", "A late fee of $50.00 was charged"),
      line("c", "owner_statement", "Your statement for September 2026"),
      line("d", "work_order", "Work order #1 at Property 1: Gate stuck"),
    ]);
    render(<EmailsCard about="tenant" aboutId="t1" name="Tenant 1" />);
    const items = within(await screen.findByRole("list", { name: "Email, Tenant 1" })).getAllByRole("listitem");
    expect(items.map((item) => item.querySelectorAll("p")[1]?.textContent?.split(" to ")[0])).toEqual([
      "Rent reminder", "Late fee notice", "Monthly statement", "Work order",
    ]);
  });

  it("lists an application's notices with no Write email", async () => {
    const fetch = server([{
      id: "n1", kind: "adverse_action", to: "a@example.com", subject: "About your rental application: Cedar House, Unit 2",
      body: "Your application was declined.", at: "2026-10-16T15:00:00Z", by: null, status: "waiting", error: null,
    }]);
    render(<EmailsCard about="application" aboutId="app1" name="Applicant 1" emailOn={false} />);
    const list = await screen.findByRole("list", { name: "Email, Applicant 1" });
    expect(list.textContent).toContain("Adverse action notice to a@example.com · Sent by the PMS");
    expect(screen.queryByRole("button", { name: "Write email" })).toBeNull();
    expect(screen.queryByText(/sends no email yet/)).toBeNull();
    expect(fetch.mock.calls[0]?.[0]).toBe("/api/emails?about=application&about_id=app1");
  });

  it("says when a person's email can't be read", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({ detail: "sign in first" }), { status: 401 })));
    render(<EmailsCard about="tenant" aboutId="t1" name="Tenant 1" />);
    expect((await screen.findByRole("alert")).textContent).toBe("sign in first");
  });
});

describe("WriteEmail", () => {
  it("shows its note and closes from Close", () => {
    const close = vi.fn();
    render(<WriteEmail title="Email tenants of Cedar House" note="Everyone gets a copy." onSend={async () => ""} onClose={close} />);
    expect(screen.getByText("Everyone gets a copy.")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Close" }));
    expect(close).toHaveBeenCalled();
  });
});
