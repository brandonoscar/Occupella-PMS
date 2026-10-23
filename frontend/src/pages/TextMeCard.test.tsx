import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { MyTexts } from "../texts";
import { TextMeCard } from "./TextMeCard";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

const NONE: MyTexts = { sending: true, consent: null, phone_on_file: "+12025550123" };
const YES: MyTexts = {
  sending: true, phone_on_file: "+12025550123",
  consent: { phone: "+12025550145", consented: true, how: "portal", at: "2026-10-23T15:00:00Z", by: null },
};
const NO: MyTexts = { ...YES, consent: { ...YES.consent!, consented: false } };

function server(first: MyTexts, answers: (MyTexts | string)[]) {
  const fetch = vi.fn(async (_path: string, init?: RequestInit) => {
    if (init?.method === "POST") {
      const answer = answers.shift();
      return typeof answer === "string"
        ? new Response(JSON.stringify({ detail: answer }), { status: 422 })
        : new Response(JSON.stringify(answer), { status: 201 });
    }
    return new Response(JSON.stringify(first));
  });
  vi.stubGlobal("fetch", fetch);
  return fetch;
}

describe("TextMeCard", () => {
  it("lets a tenant say yes at a number, then stop", async () => {
    const fetch = server(NONE, [YES, NO]);
    render(<TextMeCard />);
    expect((await screen.findByTestId("my-text-consent")).textContent).toBe("You haven't said whether we may text you.");
    expect((screen.getByLabelText("Mobile number") as HTMLInputElement).value).toBe("(202) 555-0123");
    expect(screen.queryByRole("button", { name: "Stop texting me" })).toBeNull();
    fireEvent.change(screen.getByLabelText("Mobile number"), { target: { value: "(202) 555-0145" } });
    fireEvent.click(screen.getByRole("button", { name: "Text me at this number" }));
    expect(await screen.findByText("We'll text you at (202) 555-0145.")).toBeTruthy();
    expect(screen.getByTestId("my-text-consent").textContent).toBe("Agreed to texts at (202) 555-0145 in the portal, 10/23/2026");

    fireEvent.click(screen.getByRole("button", { name: "Stop texting me" }));
    expect(await screen.findByText("No more texts.")).toBeTruthy();
    expect(fetch.mock.calls.filter(([, i]) => i?.method === "POST").map(([, i]) => JSON.parse(String(i?.body)))).toEqual([
      { phone: "(202) 555-0145", consented: true },
      { phone: "(202) 555-0145", consented: false },
    ]);
  });

  it("shows why a number was refused, and says when the company sends no texts", async () => {
    server({ ...NONE, sending: false, phone_on_file: null }, ["that isn't a US mobile number: give its ten digits"]);
    render(<TextMeCard />);
    expect(await screen.findByText(/doesn't send texts yet/)).toBeTruthy();
    fireEvent.change(screen.getByLabelText("Mobile number"), { target: { value: "12345" } });
    fireEvent.click(screen.getByRole("button", { name: "Text me at this number" }));
    expect((await screen.findByRole("alert")).textContent).toContain("isn't a US mobile number");
  });
});
