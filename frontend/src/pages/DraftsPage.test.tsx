import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { Me } from "../api";
import type { Draft } from "./DraftsPage";
import { DraftPage, DraftsPage } from "./DraftsPage";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

const STAFF: Me = {
  kind: "staff", email: "manager@example.com", name: "Manager 1", person_id: "s1", pmc_id: "p1",
  pmc_name: "Synthetic PMC", roles: ["admin", "approve", "disburse"],
  areas: ["email", "documents", "maintenance"],
};
const REPAIRS: Me = { ...STAFF, name: "Repairs 1", person_id: "s2", areas: ["maintenance"] };

const EMAIL: Draft = {
  id: "d1", kind: "email", what: "Email to Tenant 1 and 1 other: Water shutoff",
  why: "The water company posted a shutoff for tomorrow.", key: "Assistant",
  made_at: "2026-11-05T15:00:00Z", status: "waiting", decided_by: null, decided_at: null,
  edited: null, reason: null,
  proposal: {
    kind: "email", to: [{ about: "tenant", id: "t1" }, { about: "owner", id: "o1" }],
    subject: "Water shutoff", body: "Water is off from 9 to 11 tomorrow.",
  },
  names: [
    { about: "tenant", id: "t1", name: "Tenant 1", to: "tenant.one@example.com" },
    { about: "owner", id: "o1", name: "Owner 1", to: null },
  ],
  acted: null, result: null,
};

// A server holding one draft: approving or declining decides it as the API would.
function server(draft: Draft, refuse = "") {
  let held = draft;
  const fetch = vi.fn(async (path: string, init?: RequestInit) => {
    if (init?.method !== "POST") {
      if (path.startsWith("/api/drafts?")) {
        const rows = held.status === "waiting" || !path.includes("show=waiting") ? [held] : [];
        return new Response(JSON.stringify({ items: rows, total: rows.length, page: 1, per_page: 50 }), { status: 200 });
      }
      return new Response(JSON.stringify(held), { status: 200 });
    }
    if (refuse) return new Response(JSON.stringify({ detail: refuse }), { status: 409 });
    const body = JSON.parse(String(init.body)) as { proposal?: Draft["proposal"]; reason?: string };
    held = path.endsWith("/decline")
      ? { ...held, status: "declined", decided_by: "Manager 1", decided_at: "2026-11-05T16:00:00Z", reason: body.reason ?? null }
      : {
        ...held, status: "approved", decided_by: "Manager 1", decided_at: "2026-11-05T16:00:00Z",
        edited: Boolean(body.proposal), acted: body.proposal ?? held.proposal,
      };
    return new Response(JSON.stringify(held), { status: 200 });
  });
  vi.stubGlobal("fetch", fetch);
  return fetch;
}

const posted = (fetch: ReturnType<typeof server>) =>
  fetch.mock.calls.filter(([, init]) => init?.method === "POST").map(([path, init]) => [path, JSON.parse(String(init?.body))]);

describe("DraftsPage", () => {
  it("lists the drafts waiting, each opening its page, and says so when none wait", async () => {
    server(EMAIL);
    render(<DraftsPage me={STAFF} />);
    const row = await screen.findByRole("row", { name: EMAIL.what });
    const cells = within(row).getAllByRole("cell").map((cell) => cell.textContent);
    expect(cells).toEqual([EMAIL.what, EMAIL.why, "Assistant", "11/05/2026", "Waiting"]);
    expect(within(row).getByRole("link").getAttribute("href")).toBe("/drafts/d1");
  });

  it("says when nothing waits", async () => {
    server({ ...EMAIL, status: "approved" });
    render(<DraftsPage me={STAFF} />);
    expect((await screen.findByTestId("empty-state")).textContent).toContain("No drafts waiting.");
    fireEvent.click(screen.getByRole("tab", { name: "Decided" }));
    expect(await screen.findByRole("row", { name: EMAIL.what })).toBeTruthy();
  });
});

describe("DraftPage", () => {
  it("shows who an email goes to, at which address, and who gets none, before it goes", async () => {
    server(EMAIL);
    render(<DraftPage me={STAFF} id="d1" />);
    const to = await screen.findByRole("list", { name: "Recipients" });
    const people = within(to).getAllByRole("listitem").map((item) => item.textContent);
    expect(people).toEqual([
      "Tenant 1(tenant)tenant.one@example.com",
      "Owner 1(owner)No email on file: gets none",
    ]);
    expect(screen.getByText(EMAIL.why ?? "")).toBeTruthy();
    expect(within(screen.getByLabelText("What it does")).getByText("Water is off from 9 to 11 tomorrow.")).toBeTruthy();
  });

  it("asks first, then approves it as proposed", async () => {
    const fetch = server(EMAIL);
    render(<DraftPage me={STAFF} id="d1" />);
    fireEvent.click(await screen.findByRole("button", { name: "Approve" }));
    const question = screen.getByRole("alertdialog");
    expect(question.textContent).toContain("Email 1 person now, as you? An email can't be taken back once sent.");
    fireEvent.click(within(question).getByRole("button", { name: "Approve" }));
    expect(await screen.findByText(/Approved by Manager 1 on 11\/05\/2026\./)).toBeTruthy();
    expect(posted(fetch)).toEqual([["/api/drafts/d1/approve", {}]]);
    expect(screen.queryByRole("button", { name: "Approve" })).toBeNull();
  });

  it("changes the words first, then approves it as changed", async () => {
    const fetch = server(EMAIL);
    render(<DraftPage me={STAFF} id="d1" />);
    fireEvent.click(await screen.findByRole("button", { name: "Change and approve" }));
    const panel = screen.getByRole("dialog", { name: "Change and approve" });
    fireEvent.change(within(panel).getByLabelText("Subject"), { target: { value: "Water off tomorrow" } });
    fireEvent.click(within(panel).getByRole("button", { name: "Approve with changes" }));
    expect(await screen.findByText(/after changes/)).toBeTruthy();
    expect(posted(fetch)).toEqual([
      ["/api/drafts/d1/approve", { proposal: { ...EMAIL.proposal, subject: "Water off tomorrow" } }],
    ]);
    expect(within(screen.getByLabelText("As done")).getByText("Water off tomorrow")).toBeTruthy();
  });

  it("declines it saying why", async () => {
    const fetch = server(EMAIL);
    render(<DraftPage me={STAFF} id="d1" />);
    fireEvent.click(await screen.findByRole("button", { name: "Decline" }));
    const panel = screen.getByRole("dialog", { name: "Decline draft" });
    fireEvent.change(within(panel).getByLabelText("Why"), { target: { value: "Sent already." } });
    fireEvent.click(within(panel).getByRole("button", { name: "Decline" }));
    expect(await screen.findByText("Declined by Manager 1 on 11/05/2026: Sent already.")).toBeTruthy();
    expect(posted(fetch)).toEqual([["/api/drafts/d1/decline", { reason: "Sent already." }]]);
  });

  it("offers only Decline to staff whose roles don't allow what it does", async () => {
    server(EMAIL);
    render(<DraftPage me={REPAIRS} id="d1" />);
    expect(await screen.findByText("Your roles don't let you do this: you can still decline it.")).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Approve" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Change and approve" })).toBeNull();
    expect(screen.getByRole("button", { name: "Decline" })).toBeTruthy();
  });

  it("says why the server refused an approval and keeps the draft waiting", async () => {
    server(EMAIL, "this draft was declined already");
    render(<DraftPage me={STAFF} id="d1" />);
    fireEvent.click(await screen.findByRole("button", { name: "Approve" }));
    fireEvent.click(within(screen.getByRole("alertdialog")).getByRole("button", { name: "Approve" }));
    expect(await screen.findByText("this draft was declined already")).toBeTruthy();
    expect(screen.getByRole("button", { name: "Approve" })).toBeTruthy();
  });
});
