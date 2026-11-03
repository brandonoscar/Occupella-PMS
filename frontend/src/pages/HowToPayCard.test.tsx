import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { HowToPayCard } from "./HowToPayCard";

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

describe("HowToPayCard", () => {
  it("says what tenants and owners see without it, and saves the company's own words", async () => {
    const words = "Checks to Example Co.\nMail them to the office.";
    const fetch = server({ body: null }, { body: words }, { body: words });
    render(<HowToPayCard />);
    expect((await screen.findByTestId("how-to-pay")).textContent).toBe(
      'Not set. Tenants and owners see "Contact your property manager to pay."',
    );
    fireEvent.click(screen.getByRole("button", { name: "Edit how to pay us" }));
    const panel = screen.getByRole("dialog", { name: "How to pay us" });
    fireEvent.change(within(panel).getByLabelText("How to pay us"), { target: { value: words } });
    fireEvent.click(within(panel).getByRole("button", { name: /^Save/ }));
    expect(await screen.findByText("Saved how to pay us.")).toBeTruthy();
    expect(fetch.mock.calls[1]?.[0]).toBe("/api/company/how-to-pay");
    expect(JSON.parse(String(fetch.mock.calls[1]?.[1]?.body))).toEqual({ body: words });
    await vi.waitFor(() => expect(screen.getByTestId("how-to-pay").textContent).toBe(words));
  });
});
