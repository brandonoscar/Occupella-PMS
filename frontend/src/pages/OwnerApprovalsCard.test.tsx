import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { OwnerApprovalsCard } from "./OwnerApprovalsCard";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

function server(...answers: unknown[]) {
  const fetch = vi.fn(async (_path: string, _init?: RequestInit) =>
    new Response(JSON.stringify(answers.shift() ?? {}), { status: 200 }));
  vi.stubGlobal("fetch", fetch);
  return fetch;
}

describe("OwnerApprovalsCard", () => {
  it("says over what amount owners type their name, and changes it", async () => {
    const fetch = server({ sign_over: "5000.00" }, { sign_over: "2500.00" }, { sign_over: "2500.00" });
    render(<OwnerApprovalsCard />);
    expect((await screen.findByTestId("sign-over")).textContent).toBe(
      "Owners type their full name to approve bills over $5,000.00.",
    );
    fireEvent.click(screen.getByRole("button", { name: "Edit owner approvals" }));
    const panel = screen.getByRole("dialog", { name: "Owner approvals" });
    fireEvent.change(within(panel).getByLabelText("Ask for the owner's typed name over"), { target: { value: "2500.00" } });
    fireEvent.click(within(panel).getByRole("button", { name: /^Save/ }));
    expect(await screen.findByText("Saved owner approvals.")).toBeTruthy();
    expect(fetch.mock.calls[1]?.[0]).toBe("/api/company/owner-approvals");
    expect(JSON.parse(String(fetch.mock.calls[1]?.[1]?.body))).toEqual({ sign_over: "2500.00" });
  });
});
