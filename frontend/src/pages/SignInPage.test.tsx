import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { SignInPage, pmcToSignIn } from "./SignInPage";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  window.history.replaceState(null, "", "/");
});

describe("pmcToSignIn", () => {
  it("takes the PMC from the link, else from this browser's last sign-in", () => {
    expect(pmcToSignIn("?pmc=abc", "stored")).toBe("abc");
    expect(pmcToSignIn("", "stored")).toBe("stored");
    expect(pmcToSignIn("", null)).toBe("");
  });
});

// The server: whether it emails reset links (GET /api/password-reset), and what a sign-in gets.
function server(reset: boolean) {
  const fetch = vi.fn(async (_path: string, init?: RequestInit) =>
    init?.method === "POST"
      ? new Response(JSON.stringify({ detail: "enter the code from your authenticator app" }), { status: 401 })
      : new Response(JSON.stringify({ available: reset }), { status: 200 }),
  );
  vi.stubGlobal("fetch", fetch);
  return fetch;
}

describe("SignInPage", () => {
  it("asks for the authenticator code once the server says it is needed", async () => {
    window.history.replaceState(null, "", "/sign-in?pmc=p1");
    const fetch = server(false);
    render(<SignInPage />);

    expect(screen.queryByLabelText("Code from your authenticator app")).toBeNull();
    fireEvent.change(screen.getByLabelText("Email"), { target: { value: "s@example.com" } });
    fireEvent.change(screen.getByLabelText("Password"), { target: { value: "a long password" } });
    fireEvent.click(screen.getByRole("button", { name: "Sign in" }));

    expect(await screen.findByLabelText("Code from your authenticator app")).toBeTruthy();
    expect(screen.getByRole("alert").textContent).toBe("enter the code from your authenticator app");
    const [, posted] = fetch.mock.calls.find(([, init]) => init?.method === "POST") ?? [];
    expect(JSON.parse(String(posted?.body))).toEqual({
      pmc_id: "p1",
      email: "s@example.com",
      password: "a long password",
    });
  });

  it("offers a forgotten password's reset only when the server emails the link", async () => {
    window.history.replaceState(null, "", "/sign-in?pmc=p1");
    server(true);
    render(<SignInPage />);
    const link = await screen.findByRole("link", { name: "Forgot your password?" });
    expect(link.getAttribute("href")).toBe("/forgot-password?pmc=p1");
    cleanup();
    const fetch = server(false);
    render(<SignInPage />);
    await vi.waitFor(() => expect(fetch).toHaveBeenCalled());
    expect(screen.queryByRole("link", { name: "Forgot your password?" })).toBeNull();
  });

  it("without a PMC, says where to sign in from", () => {
    server(false);
    render(<SignInPage />);

    expect(screen.getByRole("status").textContent).toContain("Open the sign-in link");
  });
});
