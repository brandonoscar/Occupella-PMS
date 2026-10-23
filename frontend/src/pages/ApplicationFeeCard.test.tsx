import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { FeeSettings } from "./ApplicationFeeCard";
import { APPLICATION_FEE, ApplicationFeeCard, feeLine, readSettings } from "./ApplicationFeeCard";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

const NONE: FeeSettings = { per_applicant: "0.00", recorded_by: null, recorded_at: null };
const SET: FeeSettings = { per_applicant: "45.00", recorded_by: "Staff 1", recorded_at: "2026-10-23T12:00:00Z" };

function server(refuse = "") {
  let saved = false;
  const fetch = vi.fn(async (_path: string, init?: RequestInit) => {
    if (init?.method === "POST") {
      if (refuse) return new Response(JSON.stringify({ detail: refuse }), { status: 422 });
      saved = true;
      return new Response(JSON.stringify(SET), { status: 201 });
    }
    return new Response(JSON.stringify(saved ? SET : NONE));
  });
  vi.stubGlobal("fetch", fetch);
  return fetch;
}

describe("ApplicationFeeCard", () => {
  it("asks no fee until staff set one, then says what each applicant pays", async () => {
    const fetch = server();
    render(<ApplicationFeeCard />);

    expect((await screen.findByTestId("application-fee-setting")).textContent).toBe("No application fee");
    fireEvent.click(screen.getByRole("button", { name: "Edit application fee" }));
    const panel = screen.getByRole("dialog", { name: "Application fee" });
    fireEvent.change(within(panel).getByLabelText(/Each applicant pays/), { target: { value: " 45 " } });
    fireEvent.click(within(panel).getByRole("button", { name: "Save" }));

    expect(await screen.findByText("Application fee: $45.00 from each applicant.")).toBeTruthy();
    expect(screen.queryByRole("dialog")).toBeNull();
    await vi.waitFor(() =>
      expect(screen.getByTestId("application-fee-setting").textContent).toBe("$45.00 from each applicant"),
    );
    expect(screen.getByText(/Set 10\/23\/2026 by Staff 1\./)).toBeTruthy();
    const post = fetch.mock.calls.find(([, init]) => init?.method === "POST");
    expect(post?.[0]).toBe(APPLICATION_FEE);
    expect(JSON.parse(String(post?.[1]?.body))).toEqual({ per_applicant: "45" });
  });

  it("keeps the panel open with the server's reason when it refuses", async () => {
    server("input should be less than or equal to 1000");
    render(<ApplicationFeeCard />);
    fireEvent.click(await screen.findByRole("button", { name: "Edit application fee" }));
    const panel = screen.getByRole("dialog", { name: "Application fee" });
    fireEvent.change(within(panel).getByLabelText(/Each applicant pays/), { target: { value: "5000" } });
    fireEvent.click(within(panel).getByRole("button", { name: "Save" }));

    expect((await within(panel).findByRole("alert")).textContent).toContain("less than or equal to 1000");
    expect(screen.getByRole("dialog", { name: "Application fee" })).toBeTruthy();
  });

  it("reads an answer that isn't the settings as no fee", () => {
    expect(readSettings(null)).toEqual(NONE);
    expect(readSettings({ items: [] })).toEqual(NONE);
    expect(readSettings(SET)).toBe(SET);
    expect(feeLine({ per_applicant: "0.00" })).toBe("No application fee");
    expect(feeLine({ per_applicant: "1000.00" })).toBe("$1,000.00 from each applicant");
  });
});
