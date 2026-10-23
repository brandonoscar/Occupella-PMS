import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { TextSettings } from "../texts";
import { AutomaticTextsCard, textSettingsLines } from "./AutomaticTextsCard";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

const OFF: TextSettings = { rent_reminder_days: null, late_notices: false, sending: false };
const ON: TextSettings = { rent_reminder_days: 3, late_notices: true, sending: true };

describe("AutomaticTextsCard", () => {
  it("shows which texts go and saves new settings", async () => {
    let saved = false;
    const fetch = vi.fn(async (_path: string, init?: RequestInit) => {
      if (init?.method === "POST") {
        saved = true;
        return new Response(JSON.stringify(ON));
      }
      return new Response(JSON.stringify(saved ? ON : OFF));
    });
    vi.stubGlobal("fetch", fetch);
    render(<AutomaticTextsCard />);

    expect(await screen.findByText(/This server sends no texts yet/)).toBeTruthy();
    expect(screen.getByLabelText("Automatic texts").textContent).toBe("Rent reminders by textOffLate fee notices by textOff");
    fireEvent.click(screen.getByRole("button", { name: "Edit automatic texts" }));
    const panel = screen.getByRole("dialog", { name: "Automatic texts" });
    fireEvent.click(within(panel).getByLabelText("Rent reminders by text"));
    expect((within(panel).getByLabelText("Days before rent is due") as HTMLSelectElement).value).toBe("3");
    fireEvent.click(within(panel).getByLabelText("Late fee notices by text"));
    fireEvent.click(within(panel).getByRole("button", { name: "Save" }));

    expect(await screen.findByText("Saved the automatic texts.")).toBeTruthy();
    const post = fetch.mock.calls.find(([, init]) => init?.method === "POST");
    expect(post?.[0]).toBe("/api/texts/settings");
    expect(JSON.parse(String(post?.[1]?.body))).toEqual({ rent_reminder_days: 3, late_notices: true });
    await vi.waitFor(() => expect(screen.queryByText(/sends no texts yet/)).toBeNull());
  });

  it("words the settings", () => {
    expect(textSettingsLines({ rent_reminder_days: 1, late_notices: true })).toEqual([
      ["Rent reminders by text", "1 day before rent is due"],
      ["Late fee notices by text", "On"],
    ]);
  });
});
