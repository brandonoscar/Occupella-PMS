import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ForgotPasswordPage } from "./ForgotPasswordPage";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  window.history.replaceState(null, "", "/");
});

const ANSWER = "If that email signs in here as an owner or a tenant, a link to set a new password is on its way.";

describe("ForgotPasswordPage", () => {
  it("asks for a link for the email and says the same whoever it is", async () => {
    window.history.replaceState(null, "", "/forgot-password?pmc=p1");
    const fetch = vi.fn(async () => new Response(JSON.stringify({ detail: ANSWER }), { status: 202 }));
    vi.stubGlobal("fetch", fetch);
    render(<ForgotPasswordPage />);

    expect(screen.getByText("For owners and tenants. Staff: ask an admin for a new invite.")).toBeTruthy();
    fireEvent.change(screen.getByLabelText("Email you sign in with"), { target: { value: "o@example.com" } });
    fireEvent.click(screen.getByRole("button", { name: "Email me a link" }));

    expect((await screen.findByRole("status")).textContent).toBe(ANSWER);
    const [path, init] = fetch.mock.calls[0] as unknown as [string, RequestInit];
    expect(path).toBe("/api/password-reset");
    expect(JSON.parse(String(init.body))).toEqual({ pmc_id: "p1", email: "o@example.com" });
    expect(screen.getByRole("link", { name: "Back to sign in" }).getAttribute("href")).toBe("/sign-in?pmc=p1");
  });

  it("says why a request was refused", async () => {
    window.history.replaceState(null, "", "/forgot-password?pmc=p1");
    vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({ detail: "send JSON" }), { status: 415 })));
    render(<ForgotPasswordPage />);
    fireEvent.change(screen.getByLabelText("Email you sign in with"), { target: { value: "o@example.com" } });
    fireEvent.click(screen.getByRole("button", { name: "Email me a link" }));
    expect((await screen.findByRole("alert")).textContent).toBe("send JSON");
  });

  it("without a company to sign in to, says where to start", () => {
    localStorage.removeItem("pms.pmc");
    render(<ForgotPasswordPage />);
    expect(screen.getByRole("status").textContent).toContain("Open the sign-in link");
  });
});
