import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { InvitePage } from "./InvitePage";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  window.location.hash = "";
});

const WORDS = "OK to text me. Synthetic PMC may text this number about my rent, repairs and lease.";

function server(kind: "owner" | "staff") {
  const fetch = vi.fn(async (path: string) => {
    if (path === "/api/invites/view") {
      return new Response(JSON.stringify({
        kind, email: "o@example.com", name: "Owner One", pmc_name: "Synthetic PMC",
        expires_at: "2026-11-11T00:00:00Z", text_consent: kind === "staff" ? null : WORDS,
      }));
    }
    return new Response(JSON.stringify({ totp_uri: "otpauth://totp/x?secret=ABC" }));
  });
  vi.stubGlobal("fetch", fetch);
  return fetch;
}

function setPassword() {
  fireEvent.change(screen.getByLabelText("Choose a password (12 characters or more)"), {
    target: { value: "a long synthetic passphrase" },
  });
  fireEvent.change(screen.getByLabelText("Confirm password"), { target: { value: "a long synthetic passphrase" } });
}

describe("InvitePage", () => {
  it("asks a tenant or an owner for a phone and leaves OK to text unchecked, in the server's words", async () => {
    window.location.hash = "#tok";
    const fetch = server("owner");
    render(<InvitePage />);

    const phone = await screen.findByLabelText("Phone");
    expect(phone.hasAttribute("required")).toBe(true);
    const box = screen.getByRole("checkbox", { name: WORDS }) as HTMLInputElement;
    expect(box.checked).toBe(false);

    setPassword();
    fireEvent.change(phone, { target: { value: "(555) 555-0123" } });
    fireEvent.click(box);
    fireEvent.click(screen.getByRole("button", { name: "Set password" }));

    await vi.waitFor(() => expect(fetch).toHaveBeenCalledTimes(2));
    const [path, init] = fetch.mock.calls[1] as unknown as [string, RequestInit];
    expect([path, JSON.parse(String(init.body))]).toEqual([
      "/api/invites/accept",
      { token: "tok", password: "a long synthetic passphrase", phone: "(555) 555-0123", texts_ok: true },
    ]);
  });

  it("asks staff for neither", async () => {
    window.location.hash = "#tok";
    const fetch = server("staff");
    render(<InvitePage />);

    await screen.findByLabelText("Confirm password");
    expect(screen.queryByLabelText("Phone")).toBeNull();
    expect(screen.queryByRole("checkbox")).toBeNull();

    setPassword();
    fireEvent.click(screen.getByRole("button", { name: "Set password" }));

    await vi.waitFor(() => expect(fetch).toHaveBeenCalledTimes(2));
    const [, init] = fetch.mock.calls[1] as unknown as [string, RequestInit];
    expect(JSON.parse(String(init.body))).toEqual({ token: "tok", password: "a long synthetic passphrase" });
  });
});
