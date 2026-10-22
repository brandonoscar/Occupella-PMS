import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { Me } from "../api";
import { TwoStepCard, twoStepLine } from "./TwoStepCard";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

const TENANT: Me = {
  kind: "tenant",
  email: "tenant@example.com",
  name: "Tenant 1",
  person_id: "t1",
  pmc_id: "p1",
  pmc_name: "Synthetic PMC",
  two_step: false,
  two_step_required: false,
};
const URI = "otpauth://totp/Occupella%20PMS:tenant%40example.com?secret=JBSWY3DPEHPK3PXP&issuer=Occupella%20PMS";

// The server: the setup to show, and what confirming or turning off answers.
function server(answers: Record<string, unknown>) {
  const fetch = vi.fn(async (path: string, _init?: RequestInit) =>
    new Response(JSON.stringify(answers[path]), { status: 200 }));
  vi.stubGlobal("fetch", fetch);
  return fetch;
}

const posted = (fetch: ReturnType<typeof server>) =>
  fetch.mock.calls.map(([path, init]) => [path, JSON.parse(String(init?.body))]);

describe("TwoStepCard", () => {
  it("says whether each sign-in asks for a code, and whether the company requires it", () => {
    expect(twoStepLine({ on: false, required: false })).toBe("Off: you sign in with your password alone.");
    expect(twoStepLine({ on: true, required: false })).toBe(
      "On: each sign-in asks for your password and a code from your authenticator app.",
    );
    expect(twoStepLine({ on: true, required: true })).toBe(
      "On: each sign-in asks for your password and a code from your authenticator app. Your property" +
        " manager requires it, so it stays on.",
    );
    expect(twoStepLine({ on: false, required: true })).toBe(
      "Off. Your property manager requires it: turn it on here, or you set it up the next time you sign in.",
    );
  });

  it("turns it on: the app's setup code and key, then its first code", async () => {
    const fetch = server({
      "/api/me/two-step/start": { totp_uri: URI, secret: "JBSWY3DPEHPK3PXP" },
      "/api/me/two-step/confirm": { on: true, required: false },
    });
    const { container } = render(<TwoStepCard me={TENANT} />);
    const card = screen.getByRole("group", { name: "Two-step sign-in" });
    expect(within(card).getByTestId("two-step-state").textContent).toBe("Off: you sign in with your password alone.");

    fireEvent.click(within(card).getByRole("button", { name: "Turn on two-step sign-in" }));
    expect(await within(card).findByRole("img", { name: "Authenticator setup code" })).toBeTruthy();
    expect(container.querySelector("code")?.textContent).toBe("JBSWY3DPEHPK3PXP");
    fireEvent.change(within(card).getByLabelText("Code from your authenticator app"), { target: { value: "123456" } });
    fireEvent.click(within(card).getByRole("button", { name: "Turn on" }));

    expect(await within(card).findByText(
      "On: each sign-in asks for your password and a code from your authenticator app.",
    )).toBeTruthy();
    expect(within(card).getByRole("status").textContent).toBe(
      "Two-step sign-in is on: each sign-in now asks for a code from your authenticator app.",
    );
    expect(container.querySelector("code")).toBeNull();
    expect(posted(fetch)).toEqual([
      ["/api/me/two-step/start", {}],
      ["/api/me/two-step/confirm", { code: "123456" }],
    ]);
    // On now: a current code turns it off.
    expect(within(card).getByRole("button", { name: "Turn off" })).toBeTruthy();
  });

  it("puts the setup away on Cancel", async () => {
    server({ "/api/me/two-step/start": { totp_uri: URI, secret: "JBSWY3DPEHPK3PXP" } });
    render(<TwoStepCard me={TENANT} />);
    fireEvent.click(screen.getByRole("button", { name: "Turn on two-step sign-in" }));
    fireEvent.click(await screen.findByRole("button", { name: "Cancel" }));

    expect(screen.queryByRole("img", { name: "Authenticator setup code" })).toBeNull();
    expect(screen.getByRole("button", { name: "Turn on two-step sign-in" })).toBeTruthy();
  });

  it("turns it off with a current code", async () => {
    const fetch = server({ "/api/me/two-step/off": { on: false, required: false } });
    render(<TwoStepCard me={{ ...TENANT, kind: "owner", two_step: true }} />);
    fireEvent.change(screen.getByLabelText("Code from your authenticator app"), { target: { value: "654321" } });
    fireEvent.click(screen.getByRole("button", { name: "Turn off" }));

    expect(await screen.findByText("Off: you sign in with your password alone.")).toBeTruthy();
    expect(screen.getByRole("status").textContent).toBe(
      "Two-step sign-in is off: you sign in with your password alone.",
    );
    expect(posted(fetch)).toEqual([["/api/me/two-step/off", { code: "654321" }]]);
  });

  it("offers no way off while the company requires it", () => {
    server({});
    render(<TwoStepCard me={{ ...TENANT, two_step: true, two_step_required: true }} />);

    expect(screen.getByTestId("two-step-state").textContent).toContain("so it stays on");
    expect(screen.queryByRole("button", { name: "Turn off" })).toBeNull();
    expect(screen.queryByLabelText("Code from your authenticator app")).toBeNull();
  });

  it("says why a code was refused, and keeps the setup to try the next one", async () => {
    let refuse = "";
    vi.stubGlobal("fetch", vi.fn(async () => refuse
      ? new Response(JSON.stringify({ detail: refuse }), { status: 422 })
      : new Response(JSON.stringify({ totp_uri: URI, secret: "JBSWY3DPEHPK3PXP" }), { status: 200 })));
    render(<TwoStepCard me={TENANT} />);
    fireEvent.click(screen.getByRole("button", { name: "Turn on two-step sign-in" }));
    fireEvent.change(await screen.findByLabelText("Code from your authenticator app"), { target: { value: "000000" } });
    refuse = "that code is wrong or was already used: try the next one";
    fireEvent.click(screen.getByRole("button", { name: "Turn on" }));

    expect((await screen.findByRole("alert")).textContent).toBe(refuse);
    expect(screen.getByRole("img", { name: "Authenticator setup code" })).toBeTruthy();
    expect(screen.getByTestId("two-step-state").textContent).toBe("Off: you sign in with your password alone.");
  });
});
