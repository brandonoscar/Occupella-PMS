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

  it("falls back to the server's only company, after the link and this browser", () => {
    expect(pmcToSignIn("?pmc=abc", "stored", "only")).toBe("abc");
    expect(pmcToSignIn("", "stored", "only")).toBe("stored");
    expect(pmcToSignIn("", null, "only")).toBe("only");
    expect(pmcToSignIn("", null, null)).toBe("");
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
  it("says where to sign in instead of asking for a password when the server takes none", async () => {
    window.history.replaceState(null, "", "/sign-in?pmc=p1");
    vi.stubGlobal(
      "fetch",
      vi.fn(async (path: string) =>
        new Response(
          JSON.stringify(
            path === "/api/sign-in"
              ? { pmc_id: "p1", name: "Company A", password: false }
              : { available: false },
          ),
          { status: 200 },
        ),
      ),
    );
    render(<SignInPage />);
    expect(
      await screen.findByText(
        "You sign in to these books through the app they are part of. Sign in there to go on.",
      ),
    ).toBeTruthy();
    expect(screen.queryByLabelText("Password")).toBeNull();
  });

  it("asks for a password when the server takes one", async () => {
    window.history.replaceState(null, "", "/sign-in?pmc=p1");
    vi.stubGlobal(
      "fetch",
      vi.fn(async (path: string) =>
        new Response(
          JSON.stringify(
            path === "/api/sign-in"
              ? { pmc_id: "p1", name: "Company A", password: true }
              : { available: false },
          ),
          { status: 200 },
        ),
      ),
    );
    render(<SignInPage />);
    expect(await screen.findByLabelText("Password")).toBeTruthy();
    expect(screen.queryByText(/through the app they are part of/)).toBeNull();
  });

  it("says why when a session ended and sent the person back here (H-115)", async () => {
    window.history.replaceState(null, "", "/sign-in?pmc=p1&ended=1");
    server(false);
    render(<SignInPage />);
    expect(await screen.findByText("You were signed out after a while away. Sign in again to go on.")).toBeTruthy();
  });

  it("says nothing of it on an ordinary sign-in", async () => {
    window.history.replaceState(null, "", "/sign-in?pmc=p1");
    server(false);
    render(<SignInPage />);
    await screen.findByLabelText("Email");
    expect(screen.queryByText(/You were signed out/)).toBeNull();
  });

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

  it("without a PMC, says where to sign in from, once the server named none", async () => {
    server(false);
    render(<SignInPage />);

    expect((await screen.findByRole("status")).textContent).toContain("Open the sign-in link");
  });
});

// A server answering GET /api/sign-in with the company it holds (or none), and refusing sign-in.
function companyServer(company: { pmc_id: string | null; name: string | null }) {
  const fetch = vi.fn(async (path: string, init?: RequestInit) =>
    init?.method === "POST"
      ? new Response(JSON.stringify({ detail: "wrong email or password" }), { status: 401 })
      : path.endsWith("/api/sign-in")
        ? new Response(JSON.stringify(company), { status: 200 })
        : new Response(JSON.stringify({ available: false }), { status: 200 }),
  );
  vi.stubGlobal("fetch", fetch);
  return fetch;
}

describe("SignInPage without a link", () => {
  afterEach(() => localStorage.clear());

  it("signs in to the server's only company, and names it", async () => {
    window.history.replaceState(null, "", "/sign-in");
    const fetch = companyServer({ pmc_id: "only", name: "Cedar Demo PM" });
    render(<SignInPage />);

    expect(await screen.findByText("Cedar Demo PM")).toBeTruthy();
    fireEvent.change(screen.getByLabelText("Email"), { target: { value: "s@example.com" } });
    fireEvent.change(screen.getByLabelText("Password"), { target: { value: "a long password" } });
    fireEvent.click(screen.getByRole("button", { name: "Sign in" }));
    await vi.waitFor(() =>
      expect(fetch.mock.calls.some(([, init]) => init?.method === "POST")).toBe(true),
    );
    const [, posted] = fetch.mock.calls.find(([, init]) => init?.method === "POST") ?? [];
    expect(JSON.parse(String(posted?.body)).pmc_id).toBe("only");
  });

  it("asks for the link on a server with more companies", async () => {
    window.history.replaceState(null, "", "/sign-in");
    companyServer({ pmc_id: null, name: null });
    render(<SignInPage />);

    expect(await screen.findByText(/Open the sign-in link your property manager sent you/)).toBeTruthy();
    expect(screen.queryByLabelText("Email")).toBeNull();
  });

  it("keeps the company of the link, and names no other", async () => {
    window.history.replaceState(null, "", "/sign-in?pmc=p1");
    companyServer({ pmc_id: "only", name: "Cedar Demo PM" });
    render(<SignInPage />);

    expect(await screen.findByLabelText("Email")).toBeTruthy();
    await vi.waitFor(() => expect(screen.queryByText("Cedar Demo PM")).toBeNull());
  });
});
