import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { Me } from "../api";
import { CalendarCard, CalendarPage, webcal } from "./CalendarCard";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

const ME: Me = {
  kind: "staff", email: "staff@example.com", name: "Staff 1", person_id: "s1", pmc_id: "p1",
  pmc_name: "Synthetic PMC",
};

const GROUPS = { groups: [{ id: "g1", name: "North", properties: [] }], properties: [] };
const URL = "http://localhost:8000/api/calendar/made-up-token.ics";

// A server holding one person's calendar link: none until made, then made today.
function server(standing: { made_at: string; group_id: string | null } | null = null, refuse = "") {
  let state = standing ?? { made_at: null, group_id: null };
  const fetch = vi.fn(async (path: string, init?: RequestInit) => {
    if (path === "/api/property-groups") return new Response(JSON.stringify(GROUPS), { status: 200 });
    if (init?.method !== "POST") return new Response(JSON.stringify(state), { status: 200 });
    if (refuse) return new Response(JSON.stringify({ detail: refuse }), { status: 409 });
    if (path.endsWith("/revoke")) {
      state = { made_at: null, group_id: null };
      return new Response(null, { status: 204 });
    }
    const body = JSON.parse(String(init.body)) as { group_id?: string | null };
    state = { made_at: "2026-10-20T15:00:00Z", group_id: body.group_id ?? null };
    return new Response(JSON.stringify({ url: URL }), { status: 201 });
  });
  vi.stubGlobal("fetch", fetch);
  return fetch;
}

const posted = (fetch: ReturnType<typeof server>) =>
  fetch.mock.calls.filter(([, init]) => init?.method === "POST").map(([path, init]) => [path, JSON.parse(String(init?.body))]);

describe("CalendarPage", () => {
  it("makes a staff member's link for one property group and shows it once", async () => {
    const fetch = server();
    render(<CalendarPage me={ME} />);
    expect((await screen.findByTestId("calendar-state")).textContent).toBe("You have no calendar link.");
    await screen.findByRole("option", { name: "North" });

    fireEvent.change(screen.getByLabelText("Show"), { target: { value: "g1" } });
    fireEvent.click(screen.getByRole("button", { name: "Make a calendar link" }));

    expect(await screen.findByText("Made your calendar link.")).toBeTruthy();
    expect((screen.getByLabelText("Calendar link") as HTMLInputElement).value).toBe(URL);
    expect(screen.getByRole("link", { name: "Open in the calendar app on this device" }).getAttribute("href"))
      .toBe("webcal://localhost:8000/api/calendar/made-up-token.ics");
    expect((await screen.findByText(/^Your calendar link was made/)).textContent)
      .toBe("Your calendar link was made on 10/20/2026, for North.");
    expect(posted(fetch)).toEqual([["/api/calendar/mine", { group_id: "g1" }]]);
  });

  it("takes a standing link back once confirmed, and makes a new one in its place", async () => {
    const fetch = server({ made_at: "2026-10-01T09:00:00Z", group_id: null });
    render(<CalendarPage me={ME} />);
    expect((await screen.findByTestId("calendar-state")).textContent)
      .toBe("Your calendar link was made on 10/01/2026.");
    expect(screen.queryByLabelText("Calendar link")).toBeNull(); // a link is never shown again

    fireEvent.click(screen.getByRole("button", { name: "Make a new link" }));
    expect(await screen.findByText("Made a new calendar link; the one before stopped working.")).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: "Take the link back" }));
    const question = screen.getByRole("alertdialog");
    expect(question.textContent).toContain("Take your calendar link back? Your calendar app stops getting updates.");
    fireEvent.click(within(question).getByRole("button", { name: "Take it back" }));

    expect(await screen.findByText("Took your calendar link back.")).toBeTruthy();
    expect(screen.queryByLabelText("Calendar link")).toBeNull();
    expect((await screen.findByText("You have no calendar link.")).textContent).toBeTruthy();
    expect(posted(fetch)).toEqual([
      ["/api/calendar/mine", { group_id: null }],
      ["/api/calendar/mine/revoke", {}],
    ]);
  });

  it("says why a link was refused", async () => {
    server({ made_at: "2026-10-01T09:00:00Z", group_id: null }, "you have no calendar link to take back");
    render(<CalendarPage me={ME} />);
    fireEvent.click(await screen.findByRole("button", { name: "Take the link back" }));
    fireEvent.click(within(screen.getByRole("alertdialog")).getByRole("button", { name: "Take it back" }));

    expect((await screen.findByRole("alert")).textContent).toBe("you have no calendar link to take back");
  });
});

describe("CalendarCard", () => {
  it("makes a tenant's link with no property group to pick", async () => {
    const fetch = server();
    render(<CalendarCard path="/api/tenant/calendar" intro="Your rent due days." />);
    fireEvent.click(await screen.findByRole("button", { name: "Make a calendar link" }));

    expect(await screen.findByLabelText("Calendar link")).toBeTruthy();
    expect(screen.queryByLabelText("Show")).toBeNull();
    expect(posted(fetch)).toEqual([["/api/tenant/calendar", {}]]);
  });

  it("opens the same link in a calendar app with webcal", () => {
    expect(webcal("https://pms.example.com/api/calendar/t.ics")).toBe("webcal://pms.example.com/api/calendar/t.ics");
    expect(webcal("http://localhost/api/calendar/t.ics")).toBe("webcal://localhost/api/calendar/t.ics");
  });
});
