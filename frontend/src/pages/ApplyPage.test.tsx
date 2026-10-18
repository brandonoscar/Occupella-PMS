import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ApplyPage } from "./ApplyPage";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  window.history.replaceState(null, "", "/");
});

const LISTING = { pmc: "Synthetic PMC", property: "Property 1", unit: "Unit 1", monthly_rent: "1500.00" };

function server(apply: Response) {
  const fetch = vi.fn(async (path: string, _init?: RequestInit) =>
    path === "/api/apply/view" ? new Response(JSON.stringify(LISTING)) : apply,
  );
  vi.stubGlobal("fetch", fetch);
  return fetch;
}

describe("ApplyPage", () => {
  it("shows what the link is for, sends the application and gives the status link", async () => {
    window.history.replaceState(null, "", "/apply#link-token");
    const fetch = server(
      new Response(JSON.stringify({ status_url: "http://x/application#status" }), { status: 201 }),
    );
    render(<ApplyPage />);

    expect(await screen.findByText("Apply for Property 1, Unit 1")).toBeTruthy();
    expect(screen.getByText(/offers it at \$1,500.00 a month/)).toBeTruthy();
    fireEvent.change(screen.getByLabelText("Move-in day"), { target: { value: "2026-12-01" } });
    fireEvent.change(screen.getByLabelText("Monthly income of everyone applying, together"), {
      target: { value: "4500" },
    });
    fireEvent.change(screen.getByLabelText("Full name"), { target: { value: "Applicant A" } });
    fireEvent.change(screen.getByLabelText("Email"), { target: { value: "a@example.com" } });
    fireEvent.click(screen.getByRole("button", { name: "Add someone else applying" }));
    fireEvent.change(screen.getAllByLabelText("Full name")[1] as HTMLElement, {
      target: { value: "Applicant B" },
    });
    fireEvent.change(screen.getAllByLabelText("Email")[1] as HTMLElement, {
      target: { value: "b@example.com" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Send application" }));

    expect((await screen.findByTestId("status-url")).textContent).toBe("http://x/application#status");
    const sent = JSON.parse(String(fetch.mock.calls[1]?.[1]?.body));
    expect(sent).toEqual({
      token: "link-token",
      move_in_on: "2026-12-01",
      monthly_income: "4500",
      applicants: [
        { name: "Applicant A", email: "a@example.com" },
        { name: "Applicant B", email: "b@example.com" },
      ],
    });
  });

  it("shows every applicant the company's screening criteria", async () => {
    window.history.replaceState(null, "", "/apply#link-token");
    vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({
      pmc: "Synthetic PMC", property: "Property 1", unit: "Unit 1", monthly_rent: "1500.00",
      criteria: "Household income of 2.5 times the rent.",
    }))));
    render(<ApplyPage />);

    const criteria = await screen.findByTestId("screening-criteria");
    expect(criteria.textContent).toBe("How Synthetic PMC screens every applicantHousehold income of 2.5 times the rent.");
  });

  it("applies through a listing from the public page", async () => {
    window.history.replaceState(null, "", "/apply?listing=v1");
    const fetch = server(
      new Response(JSON.stringify({ status_url: "http://x/application#status" }), { status: 201 }),
    );
    render(<ApplyPage />);

    expect(await screen.findByText("Apply for Property 1, Unit 1")).toBeTruthy();
    fireEvent.change(screen.getByLabelText("Move-in day"), { target: { value: "2026-12-01" } });
    fireEvent.change(screen.getByLabelText("Monthly income of everyone applying, together"), {
      target: { value: "4500" },
    });
    fireEvent.change(screen.getByLabelText("Full name"), { target: { value: "Applicant A" } });
    fireEvent.change(screen.getByLabelText("Email"), { target: { value: "a@example.com" } });
    fireEvent.click(screen.getByRole("button", { name: "Send application" }));

    expect(await screen.findByTestId("status-url")).toBeTruthy();
    expect(JSON.parse(String(fetch.mock.calls[0]?.[1]?.body))).toEqual({ listing: "v1" });
    expect(JSON.parse(String(fetch.mock.calls[1]?.[1]?.body))).toMatchObject({ listing: "v1" });
    expect(JSON.parse(String(fetch.mock.calls[1]?.[1]?.body)).token).toBeUndefined();
  });

  it("says so when the link is closed", async () => {
    window.history.replaceState(null, "", "/apply#old");
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        new Response(JSON.stringify({ detail: "this application link is closed or doesn't exist" }), {
          status: 404,
        }),
      ),
    );
    render(<ApplyPage />);

    expect((await screen.findByRole("alert")).textContent).toBe(
      "this application link is closed or doesn't exist",
    );
  });
});
