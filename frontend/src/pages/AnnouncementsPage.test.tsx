import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { Me } from "../api";
import { goesTo, sentLine, wentLine } from "../announcements";
import { AnnouncementPage, AnnouncementsPage } from "./AnnouncementsPage";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

const ME: Me = { kind: "staff", email: "clerk@example.com", name: "Clerk 1", person_id: "s1", pmc_id: "p", pmc_name: "Demo PM" };
const LINE = {
  id: "a1", subject: "Water off Tuesday", words: "Every tenant living at Cedar House", by: "Clerk 1",
  at: "2026-10-22T15:00:00Z", emails: 3, sent: 2, failed: 1,
};
const PLACES = { groups: [{ id: "g1", name: "North", properties: [] }], properties: [{ id: "p1", name: "Cedar House" }] };

function server(lines = [LINE]) {
  const fetch = vi.fn(async (path: string, init?: RequestInit) => {
    if (init?.method === "POST") {
      return new Response(JSON.stringify({
        id: "a2", emailed: [{ id: "t1", name: "Tenant A", to: "a@example.com" }], without_email: ["Tenant B"],
        words: "every tenant living at Cedar House",
      }), { status: 201 });
    }
    if (path === "/api/property-groups") return new Response(JSON.stringify(PLACES), { status: 200 });
    if (path.startsWith("/api/announcements/recipients")) {
      const owners = path.includes("audience=owners");
      return new Response(JSON.stringify({
        emailed: owners ? [] : [{ id: "t1", name: "Tenant A", to: "a@example.com" }],
        without_email: owners ? ["Demo Owner A"] : ["Tenant B"],
        words: path.includes("property_id=p1") ? "every tenant living at Cedar House" : "every tenant living at the company's properties",
      }), { status: 200 });
    }
    if (path === "/api/announcements/a1") {
      return new Response(JSON.stringify({
        ...LINE, body: "From 9 to 11.",
        emails: [
          { name: "Tenant A", to: "a@example.com", status: "sent", error: null },
          { name: "Tenant C", to: "c@example.com", status: "failed", error: "mailbox unavailable" },
        ],
      }), { status: 200 });
    }
    return new Response(JSON.stringify({ items: lines, total: lines.length, page: 1, per_page: 50 }), { status: 200 });
  });
  vi.stubGlobal("fetch", fetch);
  return fetch;
}

describe("AnnouncementsPage", () => {
  it("lists what was announced, to whom and how many emails went", async () => {
    server();
    render(<AnnouncementsPage me={ME} />);
    const row = await screen.findByRole("row", { name: "Water off Tuesday" });
    expect(within(row).getByText("Every tenant living at Cedar House")).toBeTruthy();
    expect(within(row).getByText("2 of 3 sent, 1 failed")).toBeTruthy();
    expect(within(row).getByRole("link", { name: "Water off Tuesday" }).getAttribute("href")).toBe("/announcements/a1");
  });

  it("says who it will go to as it is picked, then sends it", async () => {
    const fetch = server([]);
    render(<AnnouncementsPage me={ME} />);
    expect(await screen.findByText("No announcement yet.")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "New announcement" }));
    const panel = screen.getByRole("dialog", { name: "New announcement" });
    await within(panel).findByText("Goes to 1 tenant, every tenant living at the company's properties. No email on file: Tenant B.");
    await within(panel).findByRole("option", { name: "Cedar House" });
    fireEvent.change(within(panel).getByLabelText("Of"), { target: { value: "property:p1" } });
    await within(panel).findByText("Goes to 1 tenant, every tenant living at Cedar House. No email on file: Tenant B.");
    fireEvent.change(within(panel).getByLabelText("Subject"), { target: { value: "Water off Tuesday" } });
    fireEvent.change(within(panel).getByLabelText("Message"), { target: { value: "From 9 to 11." } });
    fireEvent.click(within(panel).getByRole("button", { name: "Send announcement" }));
    expect(await screen.findByText("Emailed 1 tenant. No email on file: Tenant B.")).toBeTruthy();
    expect(screen.queryByRole("dialog")).toBeNull();
    const init = fetch.mock.calls.find(([, i]) => i?.method === "POST")?.[1];
    expect(JSON.parse(String(init?.body))).toEqual({
      audience: "tenants", property_id: "p1", subject: "Water off Tuesday", body: "From 9 to 11.",
    });
  });

  it("shows an announcement and each email with whether it went", async () => {
    server();
    render(<AnnouncementPage me={ME} id="a1" />);
    expect(await screen.findByRole("heading", { level: 1, name: "Water off Tuesday" })).toBeTruthy();
    expect(screen.getByText("From 9 to 11.")).toBeTruthy();
    const failed = screen.getByRole("row", { name: "Tenant C" });
    expect(within(failed).getByText("failed")).toBeTruthy();
    expect(within(failed).getByText("mailbox unavailable")).toBeTruthy();
    expect(within(screen.getByRole("row", { name: "Tenant A" })).getByText("sent")).toBeTruthy();
  });
});

describe("announcements", () => {
  it("words who it goes to, what it did and what went", () => {
    const found = { emailed: [], without_email: [], words: "every owner of the company's properties" };
    expect(goesTo(found, "owners")).toBe("Goes to 0 owners, every owner of the company's properties.");
    expect(sentLine({ ...found, id: "a", emailed: [{ id: "o", name: "O", to: "o@example.com" }] }, "owners")).toBe("Emailed 1 owner.");
    expect(wentLine({ ...LINE, failed: 0, sent: 3 })).toBe("3 of 3 sent");
  });
});
