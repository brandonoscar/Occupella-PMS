import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ApplicationStatusPage } from "./ApplicationStatusPage";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  window.history.replaceState(null, "", "/");
});

describe("ApplicationStatusPage", () => {
  it("shows where the application stands, with any conditions", async () => {
    window.history.replaceState(null, "", "/application#status-token");
    const fetch = vi.fn(async (_path: string, _init?: RequestInit) =>
      new Response(
        JSON.stringify({
          property: "Property 1",
          unit: "Unit 1",
          submitted_at: "2026-10-11T18:05:00Z",
          status: "conditional",
          conditions: "One more month's deposit",
        }),
      ),
    );
    vi.stubGlobal("fetch", fetch);
    render(<ApplicationStatusPage />);

    expect((await screen.findByTestId("application-status")).textContent).toBe(
      "Approved, on conditions:",
    );
    expect(screen.getByText("One more month's deposit")).toBeTruthy();
    expect(screen.getByText("Sent 10/11/2026 18:05 UTC")).toBeTruthy();
    expect(JSON.parse(String(fetch.mock.calls[0]?.[1]?.body))).toEqual({
      status_token: "status-token",
    });
  });
});
