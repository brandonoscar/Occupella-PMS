import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { AutomaticPrepaid } from "./AutomaticPrepaidCard";
import { AutomaticPrepaidCard, automaticPrepaidLine } from "./AutomaticPrepaidCard";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

const OFF: AutomaticPrepaid = { automatic: false, changed_at: null, changed_by: null };

function server(prepaid: AutomaticPrepaid, refuse?: string) {
  let now = prepaid;
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

describe("AutomaticPrepaidCard", () => {
  it("says how prepaid rent is used", () => {
    expect(automaticPrepaidLine(OFF)).toBe("Used by hand with Use prepaid rent on the lease");
    expect(automaticPrepaidLine({ ...OFF, automatic: true })).toBe("Used by itself each night for what the lease owes");
  });

  it("turns using it by itself on", async () => {
    const fetch = server(OFF);
    render(<AutomaticPrepaidCard />);
    expect((await screen.findByTestId("automatic-prepaid")).textContent).toBe(
      "Used by hand with Use prepaid rent on the lease",
    );
    fireEvent.click(screen.getByRole("button", { name: "Edit prepaid rent" }));
    const panel = screen.getByRole("dialog", { name: "Prepaid rent" });
    fireEvent.click(within(panel).getByLabelText("Use tenants' prepaid rent by itself for new rent"));
    fireEvent.click(within(panel).getByRole("button", { name: "Save" }));

    expect(await screen.findByText("Used by itself each night for what the lease owes")).toBeTruthy();
    expect(screen.queryByRole("dialog")).toBeNull();
    const post = fetch.mock.calls.find(([, init]) => init?.method === "POST");
    expect(post?.[0]).toBe("/api/rent/prepaid/automatic");
    expect(JSON.parse(String(post?.[1]?.body))).toEqual({ automatic: true });
  });

  it("keeps the panel open with why a change was refused", async () => {
    server(OFF, "your roles don't allow rent");
    render(<AutomaticPrepaidCard />);
    fireEvent.click(await screen.findByRole("button", { name: "Edit prepaid rent" }));
    const panel = screen.getByRole("dialog", { name: "Prepaid rent" });
    fireEvent.click(within(panel).getByRole("button", { name: "Save" }));
    expect((await within(panel).findByRole("alert")).textContent).toBe("your roles don't allow rent");
  });
});
