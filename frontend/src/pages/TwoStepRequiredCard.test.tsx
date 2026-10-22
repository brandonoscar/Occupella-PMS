import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { TwoStepRequired } from "./TwoStepRequiredCard";
import { TWO_STEP_REQUIRED, TwoStepRequiredCard, changedLine, requiredLine } from "./TwoStepRequiredCard";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

const CHOOSE: TwoStepRequired = { required: false, changed_at: null, changed_by: null };

function server(setting: TwoStepRequired, refuse?: string) {
  let now = setting;
  const fetch = vi.fn(async (_path: string, init?: RequestInit) => {
    if (init?.method === "POST") {
      if (refuse) return new Response(JSON.stringify({ detail: refuse }), { status: 403 });
      const { required } = JSON.parse(String(init.body)) as { required: boolean };
      now = { required, changed_at: "2026-10-22T15:04:05Z", changed_by: "Manager 1" };
    }
    return new Response(JSON.stringify(now), { status: 200 });
  });
  vi.stubGlobal("fetch", fetch);
  return fetch;
}

describe("TwoStepRequiredCard", () => {
  it("says whether owners and tenants must sign in with a code, and who changed it when", () => {
    expect(requiredLine(CHOOSE)).toBe(
      "Each owner and tenant chooses whether to turn it on in their portal. Staff always sign in with a code.",
    );
    expect(requiredLine({ ...CHOOSE, required: true })).toBe(
      "Required: every owner and tenant signs in with a code from an authenticator app too. One who hasn't" +
        " set it up does so at their next sign-in.",
    );
    expect(changedLine(CHOOSE)).toBe("");
    expect(changedLine({ required: true, changed_at: "2026-10-22T15:04:05Z", changed_by: "Manager 1" })).toBe(
      "Changed by Manager 1 on 10/22/2026.",
    );
    expect(changedLine({ required: true, changed_at: "2026-10-22T15:04:05Z", changed_by: null })).toBe(
      "Changed on 10/22/2026.",
    );
  });

  it("requires it once staff say yes, and stops requiring it the same way", async () => {
    const fetch = server(CHOOSE);
    render(<TwoStepRequiredCard />);
    fireEvent.click(await screen.findByRole("button", { name: "Require two-step sign-in" }));
    const asked = screen.getByRole("alertdialog");
    expect(asked.textContent).toContain(
      "Require two-step sign-in of every owner and tenant? No one is signed out: each one without it sets it up at their next sign-in.",
    );
    fireEvent.click(within(asked).getByRole("button", { name: "Require it" }));

    expect(await screen.findByText("Changed by Manager 1 on 10/22/2026.")).toBeTruthy();
    expect(screen.getByTestId("two-step-required").textContent).toContain("Required: every owner and tenant");
    expect(screen.getByRole("status").textContent).toBe(
      "Owners and tenants now sign in with a code too, each from their next sign-in.",
    );
    const post = fetch.mock.calls.find(([, init]) => init?.method === "POST");
    expect(post?.[0]).toBe(TWO_STEP_REQUIRED);
    expect(JSON.parse(String(post?.[1]?.body))).toEqual({ required: true });

    fireEvent.click(screen.getByRole("button", { name: "Stop requiring it" }));
    fireEvent.click(within(screen.getByRole("alertdialog")).getByRole("button", { name: "Stop requiring it" }));
    expect(await screen.findByText("Owners and tenants now choose whether to sign in with a code.")).toBeTruthy();
    expect(screen.getByTestId("two-step-required").textContent).toContain("Each owner and tenant chooses");
  });

  it("asks nothing when staff say no, and says why a change was refused", async () => {
    const fetch = server(CHOOSE, "your roles don't let you change who can sign in: an admin can add it");
    render(<TwoStepRequiredCard />);
    fireEvent.click(await screen.findByRole("button", { name: "Require two-step sign-in" }));
    fireEvent.click(within(screen.getByRole("alertdialog")).getByRole("button", { name: "Cancel" }));
    expect(fetch.mock.calls.filter(([, init]) => init?.method === "POST")).toEqual([]);

    fireEvent.click(screen.getByRole("button", { name: "Require two-step sign-in" }));
    fireEvent.click(within(screen.getByRole("alertdialog")).getByRole("button", { name: "Require it" }));
    expect((await screen.findByRole("alert")).textContent).toBe(
      "your roles don't let you change who can sign in: an admin can add it",
    );
    expect(screen.getByTestId("two-step-required").textContent).toContain("Each owner and tenant chooses");
  });
});
