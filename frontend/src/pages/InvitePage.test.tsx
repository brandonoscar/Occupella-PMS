import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { InvitePage, enrollWords } from "./InvitePage";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  localStorage.clear();
  window.history.replaceState(null, "", "/");
});

const URI = "otpauth://totp/Occupella%20PMS:o%40example.com?secret=JBSWY3DPEHPK3PXP&issuer=Occupella%20PMS";

// The server: the invite's details, and what setting the password answers.
function server(kind: string, accepted: unknown) {
  const fetch = vi.fn(async (path: string) =>
    new Response(
      JSON.stringify(
        path === "/api/invites/view"
          ? { kind, email: "o@example.com", name: "Owner 1", pmc_name: "Synthetic PMC", expires_at: "2026-10-29T00:00:00Z" }
          : accepted,
      ),
      { status: 200 },
    ),
  );
  vi.stubGlobal("fetch", fetch);
  return fetch;
}

async function setPassword() {
  fireEvent.change(await screen.findByLabelText("Choose a password (12 characters or more)"), {
    target: { value: "a long synthetic passphrase" },
  });
  fireEvent.change(screen.getByLabelText("The same password again"), { target: { value: "a long synthetic passphrase" } });
  fireEvent.click(screen.getByRole("button", { name: "Set password" }));
}

describe("InvitePage", () => {
  it("says why the authenticator is set up, for staff and for an owner or a tenant", () => {
    expect(enrollWords("staff")).toBe("Staff also sign in with a code from an authenticator app.");
    expect(enrollWords("owner")).toBe(
      "Your property manager requires two-step sign-in: you sign in with a code from an authenticator app too.",
    );
  });

  it("has an owner whose company requires two-step sign-in set it up after their password", async () => {
    window.location.hash = "#an-invite-token-of-twenty-chars";
    server("owner", { totp_uri: URI });
    const { container } = render(<InvitePage />);
    await setPassword();

    expect(await screen.findByText(/^Your property manager requires two-step sign-in: you sign in/)).toBeTruthy();
    expect(container.querySelector("code")?.textContent).toBe("JBSWY3DPEHPK3PXP");
    expect(screen.getByRole("button", { name: "Finish" })).toBeTruthy();
  });

  it("after a reset that kept two-step sign-in on, sends them to sign in with a code", async () => {
    window.location.hash = "#an-invite-token-of-twenty-chars";
    server("tenant", { pmc_id: "p1", sign_in_with_code: true });
    render(<InvitePage />);
    await setPassword();

    expect((await screen.findByRole("status")).textContent).toBe(
      "Your new password is set. Two-step sign-in stays on: sign in with your new password and the code from" +
        " your authenticator app.",
    );
    expect(screen.getByRole("link", { name: "Sign in" }).getAttribute("href")).toBe("/sign-in?pmc=p1");
    expect(localStorage.getItem("pms.pmc")).toBe("p1");
    expect(screen.queryByLabelText("Choose a password (12 characters or more)")).toBeNull();
  });
});
