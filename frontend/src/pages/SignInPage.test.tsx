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

describe("SignInPage", () => {
  it("asks for the authenticator code once the server says it is needed", async () => {
    window.history.replaceState(null, "", "/sign-in?pmc=p1");
    const fetch = vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ detail: "enter the code from your authenticator app" }), {
        status: 401,
      }),
    );
    vi.stubGlobal("fetch", fetch);
    render(<SignInPage />);

    expect(screen.queryByLabelText("Code from your authenticator app")).toBeNull();
    fireEvent.change(screen.getByLabelText("Email"), { target: { value: "s@example.com" } });
    fireEvent.change(screen.getByLabelText("Password"), { target: { value: "a long password" } });
    fireEvent.click(screen.getByRole("button", { name: "Sign in" }));

    expect(await screen.findByLabelText("Code from your authenticator app")).toBeTruthy();
    expect(screen.getByRole("alert").textContent).toBe("enter the code from your authenticator app");
    expect(JSON.parse(fetch.mock.calls[0]?.[1].body)).toEqual({
      pmc_id: "p1",
      email: "s@example.com",
      password: "a long password",
    });
  });

  it("without a PMC, says where to sign in from", () => {
    render(<SignInPage />);

    expect(screen.getByRole("status").textContent).toContain("Open the sign-in link");
  });
});
