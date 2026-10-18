import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { AutomaticFees } from "./AutomaticFeesCard";
import { AutomaticFeesCard, automaticFeesLine } from "./AutomaticFeesCard";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

const OFF: AutomaticFees = { automatic: false, day_of_month: 1, changed_at: null, changed_by: null };

function server(fees: AutomaticFees, refuse?: string) {
  let now = fees;
  const fetch = vi.fn(async (_path: string, init?: RequestInit) => {
    if (init?.method === "POST") {
      if (refuse) return new Response(JSON.stringify({ detail: refuse }), { status: 403 });
      now = { ...now, ...JSON.parse(String(init.body)), changed_by: "Staff 1" };
    }
    return new Response(JSON.stringify(now), { status: 200 });
  });
  vi.stubGlobal("fetch", fetch);
  return fetch;
}

describe("AutomaticFeesCard", () => {
  it("says how the management fees are taken", () => {
    expect(automaticFeesLine(OFF)).toBe("Collected by hand on Management fees");
    expect(automaticFeesLine({ ...OFF, automatic: true, day_of_month: 22 })).toBe(
      "Taken by themselves: last month's, from the 22nd of each month",
    );
  });

  it("turns taking them each month on, from a day of the month", async () => {
    const fetch = server(OFF);
    render(<AutomaticFeesCard />);
    expect((await screen.findByTestId("automatic-fees")).textContent).toBe("Collected by hand on Management fees");
    fireEvent.click(screen.getByRole("button", { name: "Edit monthly fees" }));
    const panel = screen.getByRole("dialog", { name: "Monthly management fees" });
    expect(within(panel).queryByLabelText("From this day of the month")).toBeNull();
    fireEvent.click(within(panel).getByLabelText("Take last month's management fees by themselves"));
    fireEvent.change(within(panel).getByLabelText("From this day of the month"), { target: { value: "5" } });
    fireEvent.click(within(panel).getByRole("button", { name: "Save" }));

    expect(await screen.findByText("Taken by themselves: last month's, from the 5th of each month")).toBeTruthy();
    expect(screen.queryByRole("dialog")).toBeNull();
    const post = fetch.mock.calls.find(([, init]) => init?.method === "POST");
    expect(post?.[0]).toBe("/api/payables/management-fees/automatic");
    expect(JSON.parse(String(post?.[1]?.body))).toEqual({ automatic: true, day_of_month: 5 });
  });

  it("keeps the panel open with why a change was refused", async () => {
    server(OFF, "your roles don't allow owners' money");
    render(<AutomaticFeesCard />);
    fireEvent.click(await screen.findByRole("button", { name: "Edit monthly fees" }));
    const panel = screen.getByRole("dialog", { name: "Monthly management fees" });
    fireEvent.click(within(panel).getByRole("button", { name: "Save" }));
    expect((await within(panel).findByRole("alert")).textContent).toBe("your roles don't allow owners' money");
  });
});
