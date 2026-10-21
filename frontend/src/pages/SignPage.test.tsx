import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { SigningView } from "../signing";
import { SignPage } from "./SignPage";

const TOKEN = "a-signing-token-of-enough-length";

beforeEach(() => {
  window.history.replaceState(null, "", `/sign#${TOKEN}`);
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

const VIEW: SigningView = {
  company: "Synthetic PMC",
  document_title: "Residential lease",
  home: "Property 1, Unit 1",
  starts_on: "2027-01-01",
  ends_on: null,
  monthly_rent: "1500.00",
  signer: "Applicant A",
  role: "tenant",
  document_sha256: "ab".repeat(32),
  disclosure: "Synthetic PMC asks you to receive and sign this lease electronically.\n\nPaper instead.",
  disclosure_sha256: "cd".repeat(32),
  status: "open",
  consented: false,
  opened: false,
  signed_at: null,
  signers: [
    { name: "Applicant A", role: "tenant", signed: false },
    { name: "Applicant B", role: "guarantor", signed: true },
  ],
};

function server(answers: Record<string, unknown>, refuse: Record<string, string> = {}) {
  const fetch = vi.fn(async (path: string, _init?: RequestInit) => {
    if (path in refuse) return new Response(JSON.stringify({ detail: refuse[path] }), { status: 409 });
    if (path === "/api/signing/document" || path === "/api/signing/certificate") {
      return new Response("%PDF-1.4", { status: 200 });
    }
    return new Response(JSON.stringify(answers[path] ?? VIEW), { status: 200 });
  });
  vi.stubGlobal("fetch", fetch);
  vi.stubGlobal("URL", { createObjectURL: () => "blob:file", revokeObjectURL: () => undefined });
  vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => undefined);
  return fetch;
}

const bodies = (fetch: ReturnType<typeof server>) =>
  fetch.mock.calls.map(([path, init]) => [path, JSON.parse(String(init?.body))]);

describe("SignPage", () => {
  it("shows the lease and asks the signer to agree to sign electronically first", async () => {
    const fetch = server({ "/api/signing/consent": { ...VIEW, consented: true } });
    render(<SignPage />);

    expect(await screen.findByRole("heading", { name: "Sign your lease with Synthetic PMC" })).toBeTruthy();
    const lease = screen.getByRole("region", { name: "The lease" });
    expect(lease.textContent).toContain("From 01/01/2027, month to month, $1,500.00 a month");
    expect(lease.textContent).toContain("For Applicant A, as a tenant");
    expect(lease.textContent).toContain("Applicant BSigned");
    expect(screen.getByTestId("disclosure").textContent).toBe(VIEW.disclosure);
    const go = screen.getByRole("button", { name: "Continue" });
    expect(go.hasAttribute("disabled")).toBe(true);
    fireEvent.click(screen.getByLabelText("I have read this and agree to receive and sign this lease electronically"));
    fireEvent.click(go);

    expect(await screen.findByRole("button", { name: "Open the lease (PDF)" })).toBeTruthy();
    expect(bodies(fetch)).toEqual([
      ["/api/signing/view", { token: TOKEN }],
      ["/api/signing/consent", { token: TOKEN, disclosure_sha256: VIEW.disclosure_sha256 }],
    ]);
  });

  it("asks before the signer chooses paper, and then says so", async () => {
    const fetch = server({ "/api/signing/paper": { ...VIEW, status: "paper" } });
    render(<SignPage />);
    fireEvent.click(await screen.findByRole("button", { name: "I'd rather sign on paper" }));
    fireEvent.click(screen.getByRole("button", { name: "Sign on paper" }));

    expect(await screen.findByText(/You asked to sign on paper/)).toBeTruthy();
    expect(bodies(fetch).at(-1)).toEqual(["/api/signing/paper", { token: TOKEN }]);
  });

  it("signs only once the lease is opened and the signer says they sign it", async () => {
    const signed = { ...VIEW, consented: true, opened: true, status: "signed", signed_at: "2026-10-20T15:00:00Z" };
    const fetch = server({
      "/api/signing/view": { ...VIEW, consented: true },
      "/api/signing/sign": signed,
    });
    render(<SignPage />);
    const sign = await screen.findByRole("button", { name: "Sign the lease" });
    fireEvent.change(screen.getByLabelText("Type your name as it is on the lease (Applicant A)"), {
      target: { value: "Applicant A" },
    });
    fireEvent.click(screen.getByLabelText("I have read the lease and I sign it"));
    expect(sign.hasAttribute("disabled")).toBe(true);

    fireEvent.click(screen.getByRole("button", { name: "Open the lease (PDF)" }));
    expect(await screen.findByText(/You opened the lease/)).toBeTruthy();
    expect(sign.hasAttribute("disabled")).toBe(false);
    fireEvent.click(sign);

    expect((await screen.findByTestId("sign-status")).textContent).toContain("You signed on 10/20/2026");
    fireEvent.click(screen.getByRole("button", { name: "Download the signing certificate (PDF)" }));
    await vi.waitFor(() => expect(fetch.mock.calls.length).toBe(4));
    expect(bodies(fetch).slice(1)).toEqual([
      ["/api/signing/document", { token: TOKEN }],
      [
        "/api/signing/sign",
        { token: TOKEN, typed_name: "Applicant A", document_sha256: VIEW.document_sha256, drawing_base64: null },
      ],
      ["/api/signing/certificate", { token: TOKEN }],
    ]);
  });

  it("says why a refused step was refused", async () => {
    server({ "/api/signing/view": { ...VIEW, consented: true } }, {
      "/api/signing/document": "this signing link has run out; ask for a new one",
    });
    render(<SignPage />);
    fireEvent.click(await screen.findByRole("button", { name: "Open the lease (PDF)" }));

    expect((await screen.findAllByText("this signing link has run out; ask for a new one")).length).toBeGreaterThan(0);
  });

  it.each([
    ["void", "Synthetic PMC withdrew this lease offer. It takes no signature."],
    ["expired", "This link has run out. Ask Synthetic PMC for a new one."],
  ] as const)("says a %s link signs nothing", async (status, words) => {
    server({ "/api/signing/view": { ...VIEW, status } });
    render(<SignPage />);

    expect(await screen.findByText(words)).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Continue" })).toBeNull();
  });

  it("says a link that isn't one of ours is not found", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({ detail: "this signing link is not one of ours" }), { status: 404 })));
    render(<SignPage />);

    expect(await screen.findByText("this signing link is not one of ours")).toBeTruthy();
  });
});
