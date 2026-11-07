import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { InvitePage, qrDataUrl, secretOf } from "./InvitePage";

const URI = "otpauth://totp/Occupella%20PMS:s%40example.com?secret=JBSWY3DPEHPK3PXP&issuer=Occupella%20PMS";

describe("authenticator setup", () => {
  it("shows the key from the setup link, to type into the app", () => {
    expect(secretOf(URI)).toBe("JBSWY3DPEHPK3PXP");
  });

  it("draws the setup link as a QR code image", () => {
    expect(qrDataUrl(URI)).toMatch(/^data:image\/gif;base64,/);
  });
});

describe("InvitePage", () => {
  afterEach(() => {
    cleanup();
    vi.unstubAllGlobals();
    window.location.hash = "";
  });

  const DETAILS = {
    kind: "tenant", email: "t@example.com", name: "Tenant Sample", pmc_name: "Synthetic PMC",
    expires_at: "2026-11-13T00:00:00Z", text_consent: "OK to text me",
  };

  function server(details: object) {
    const fetch = vi.fn(async (path: string, _init?: RequestInit) => {
      if (path === "/api/invites/view") return new Response(JSON.stringify(details), { status: 200 });
      return new Response(JSON.stringify({ detail: "enter the code from your authenticator app" }), { status: 401 });
    });
    vi.stubGlobal("fetch", fetch);
    return fetch;
  }

  async function setPassword() {
    fireEvent.change(await screen.findByLabelText("Choose a password (12 characters or more)"), { target: { value: "a long new password" } });
    fireEvent.change(screen.getByLabelText("Confirm password"), { target: { value: "a long new password" } });
    fireEvent.change(screen.getByLabelText("Phone"), { target: { value: "(555) 555-0123" } });
  }

  it("asks a reset with two-step sign-in on for the code, and sends it with the password", async () => {
    window.location.hash = "#made-up-token-of-twenty-chars";
    const fetch = server({ ...DETAILS, two_step: true });
    render(<InvitePage />);
    await setPassword();
    fireEvent.change(screen.getByLabelText("Code from your authenticator app"), { target: { value: "123456" } });
    fireEvent.click(screen.getByRole("button", { name: "Set password" }));
    expect(await screen.findByText("enter the code from your authenticator app")).toBeTruthy();
    const [, init] = fetch.mock.calls.find(([path]) => path === "/api/invites/accept")!;
    expect(JSON.parse(String(init?.body))).toEqual({
      token: "made-up-token-of-twenty-chars", password: "a long new password",
      phone: "(555) 555-0123", texts_ok: false, code: "123456",
    });
  });

  it("asks no code of an invite that turns two-step sign-in off", async () => {
    window.location.hash = "#made-up-token-of-twenty-chars";
    const fetch = server({ ...DETAILS, two_step: false });
    render(<InvitePage />);
    await setPassword();
    expect(screen.queryByLabelText("Code from your authenticator app")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Set password" }));
    await screen.findByText("enter the code from your authenticator app");
    const [, init] = fetch.mock.calls.find(([path]) => path === "/api/invites/accept")!;
    expect(JSON.parse(String(init?.body))).not.toHaveProperty("code");
  });
});
