import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { TwoStepCard } from "./TwoStepCard";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

const URI = "otpauth://totp/Occupella%20PMS:t%40example.com?secret=JBSWY3DPEHPK3PXP&issuer=Occupella%20PMS";
const WRONG = "that code is wrong or was already used: try the next one";

// A server holding one owner's or tenant's two-step sign-in: off until a code confirms it, and
// refusing any code but 123456.
function server(on = false) {
  let state = on;
  const fetch = vi.fn(async (path: string, init?: RequestInit) => {
    if (init?.method !== "POST") return new Response(JSON.stringify({ on: state }), { status: 200 });
    if (path.endsWith("/start")) return new Response(JSON.stringify({ uri: URI }), { status: 200 });
    const { code } = JSON.parse(String(init.body)) as { code: string };
    if (code !== "123456") return new Response(JSON.stringify({ detail: WRONG }), { status: 422 });
    state = path.endsWith("/confirm");
    return new Response(JSON.stringify({ on: state }), { status: 200 });
  });
  vi.stubGlobal("fetch", fetch);
  return fetch;
}

const posted = (fetch: ReturnType<typeof server>) =>
  fetch.mock.calls.filter(([, init]) => init?.method === "POST").map(([path, init]) => [path, JSON.parse(String(init?.body))]);

const state = async () => (await screen.findByTestId("two-step-state")).textContent;

describe("TwoStepCard", () => {
  it("sets up an authenticator, refuses a wrong code and turns it on with the right one", async () => {
    const fetch = server();
    render(<TwoStepCard />);
    expect(await state()).toBe("Two-step sign-in is off: signing in asks for your password alone.");

    fireEvent.click(screen.getByRole("button", { name: "Set up two-step sign-in" }));
    const qr = await screen.findByRole("img", { name: "Authenticator setup code" });
    expect(qr.getAttribute("src")).toMatch(/^data:image\/gif;base64,/);
    expect(screen.getByText("JBSWY3DPEHPK3PXP")).toBeTruthy();

    fireEvent.change(screen.getByLabelText("Code from your authenticator app"), { target: { value: "000000" } });
    fireEvent.click(screen.getByRole("button", { name: "Turn on two-step sign-in" }));
    expect(await screen.findByText(WRONG)).toBeTruthy();

    fireEvent.change(screen.getByLabelText("Code from your authenticator app"), { target: { value: "123456" } });
    fireEvent.click(screen.getByRole("button", { name: "Turn on two-step sign-in" }));
    expect(await screen.findByText(
      "Two-step sign-in is on: signing in asks for a code from your authenticator app too.",
    )).toBeTruthy();
    expect(screen.getByRole("status").textContent)
      .toBe("Two-step sign-in is on. Anywhere else you were signed in, you're signed out.");
    expect(screen.queryByRole("img", { name: "Authenticator setup code" })).toBeNull();
    expect(posted(fetch)).toEqual([
      ["/api/me/two-step/start", {}],
      ["/api/me/two-step/confirm", { code: "000000" }],
      ["/api/me/two-step/confirm", { code: "123456" }],
    ]);
  });

  it("cancels a setup without turning anything on", async () => {
    const fetch = server();
    render(<TwoStepCard />);
    fireEvent.click(await screen.findByRole("button", { name: "Set up two-step sign-in" }));
    await screen.findByRole("img", { name: "Authenticator setup code" });
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
    expect(await screen.findByRole("button", { name: "Set up two-step sign-in" })).toBeTruthy();
    expect(posted(fetch)).toEqual([["/api/me/two-step/start", {}]]);
  });

  it("turns two-step sign-in off with a code, and says how to get back in without the phone", async () => {
    const fetch = server(true);
    render(<TwoStepCard />);
    expect(await state())
      .toBe("Two-step sign-in is on: signing in asks for a code from your authenticator app too.");
    expect(screen.getByText(/Lost your phone\? Ask your property manager for a new invite/)).toBeTruthy();

    fireEvent.change(screen.getByLabelText("Code from your authenticator app"), { target: { value: "123456" } });
    fireEvent.click(screen.getByRole("button", { name: "Turn off two-step sign-in" }));
    expect(await screen.findByText("Two-step sign-in is off: signing in asks for your password alone.")).toBeTruthy();
    expect(screen.getByRole("status").textContent).toBe("Two-step sign-in is off.");
    expect(posted(fetch)).toEqual([["/api/me/two-step/off", { code: "123456" }]]);
  });

  it("says why it couldn't load", async () => {
    vi.stubGlobal("fetch", vi.fn(async () =>
      new Response(JSON.stringify({ detail: "only an owner or a tenant may do this" }), { status: 403 })));
    render(<TwoStepCard />);
    expect(await screen.findByText("only an owner or a tenant may do this")).toBeTruthy();
  });
});
