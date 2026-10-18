import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { ScreeningCriteria } from "./ScreeningCriteriaCard";
import { SCREENING_CRITERIA, ScreeningCriteriaCard, criteriaLine } from "./ScreeningCriteriaCard";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

const DEFAULT: ScreeningCriteria = {
  id: null, approve_multiple: "3.00", conditional_multiple: "2.00", written: null, recorded_at: null, recorded_by: null,
};
const SET: ScreeningCriteria = {
  id: "c1", approve_multiple: "2.50", conditional_multiple: "1.50", written: "Income of 2.5 times the rent.",
  recorded_at: "2026-10-18T12:00:00Z", recorded_by: "Staff 1",
};

function server(first: ScreeningCriteria, after: ScreeningCriteria = SET, refuse = "") {
  let saved = false;
  const fetch = vi.fn(async (_path: string, init?: RequestInit) => {
    if (init?.method === "POST") {
      if (refuse) return new Response(JSON.stringify({ detail: refuse }), { status: 422 });
      saved = true;
      return new Response(JSON.stringify(after), { status: 201 });
    }
    return new Response(JSON.stringify(saved ? after : first));
  });
  vi.stubGlobal("fetch", fetch);
  return fetch;
}

describe("ScreeningCriteriaCard", () => {
  it("shows the partner's criteria until the company sets its own, then saves new ones", async () => {
    const fetch = server(DEFAULT);
    render(<ScreeningCriteriaCard />);

    expect((await screen.findByTestId("screening-criteria")).textContent).toBe(
      "Approved at 3 times the rent or more; on conditions at 2 times the rent; declined below",
    );
    expect(screen.getByText("The screening partner's own, until the company sets its criteria.")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Edit screening criteria" }));
    const panel = screen.getByRole("dialog", { name: "Screening criteria" });
    fireEvent.change(within(panel).getByLabelText("Approve from (times the rent)"), { target: { value: "2.5" } });
    fireEvent.change(within(panel).getByLabelText("Approve on conditions from (times the rent)"), { target: { value: "1.5" } });
    fireEvent.change(within(panel).getByLabelText("In words, for applicants (optional)"), {
      target: { value: " Income of 2.5 times the rent. " },
    });
    fireEvent.click(within(panel).getByRole("button", { name: "Save" }));

    expect(await screen.findByText("Saved the screening criteria.")).toBeTruthy();
    expect(screen.queryByRole("dialog")).toBeNull();
    await vi.waitFor(() => expect(screen.getByTestId("screening-criteria").textContent).toBe(
      "Approved at 2.5 times the rent or more; on conditions at 1.5 times the rent; declined below",
    ));
    expect(screen.getByText("Income of 2.5 times the rent.")).toBeTruthy();
    expect(screen.getByText(/^Set 10\/18\/2026 by Staff 1\./)).toBeTruthy();
    const post = fetch.mock.calls.find(([, init]) => init?.method === "POST");
    expect(post?.[0]).toBe(SCREENING_CRITERIA);
    expect(JSON.parse(String(post?.[1]?.body))).toEqual({
      approve_multiple: "2.5", conditional_multiple: "1.5", written: "Income of 2.5 times the rent.",
    });
  });

  it("keeps the panel open with why the server refused", async () => {
    server(DEFAULT, SET, "conditions start at no more than what is approved outright");
    render(<ScreeningCriteriaCard />);
    fireEvent.click(await screen.findByRole("button", { name: "Edit screening criteria" }));
    const panel = screen.getByRole("dialog", { name: "Screening criteria" });
    fireEvent.click(within(panel).getByRole("button", { name: "Save" }));

    expect(await within(panel).findByText("conditions start at no more than what is approved outright")).toBeTruthy();
  });

  it("says the criteria in a line", () => {
    expect(criteriaLine(SET)).toBe("Approved at 2.5 times the rent or more; on conditions at 1.5 times the rent; declined below");
  });
});
