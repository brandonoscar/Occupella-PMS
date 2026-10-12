import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ESignPage } from "./ESignPage";

beforeEach(() => {
  window.history.replaceState(null, "", "/esign#a-signing-token-of-enough-length");
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

const ENVELOPE = {
  partner: "Fake e-sign partner",
  document_title: "Lease renewal",
  signer: "Tenant 1",
  document_sha256: "ab".repeat(32),
  signed: false,
};

function server(view: Response, sign: Response = new Response(null, { status: 204 })) {
  const fetch = vi.fn(async (path: string, _init?: RequestInit) =>
    path === "/api/fake-esign/view" ? view : sign,
  );
  vi.stubGlobal("fetch", fetch);
  return fetch;
}

const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status });

describe("ESignPage", () => {
  it("says it is a stand-in, and signs with the signer's name", async () => {
    const fetch = server(json(ENVELOPE));
    render(<ESignPage />);

    expect(await screen.findByRole("heading", { name: "Lease renewal" })).toBeTruthy();
    expect(screen.getByText(/Fake e-sign partner: a stand-in/).textContent).toContain(
      "Typing your name here is not a real signature.",
    );
    expect(screen.getByText("For Tenant 1")).toBeTruthy();
    fireEvent.change(screen.getByLabelText("Type your name as it is on the lease"), {
      target: { value: "Tenant 1" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Sign" }));

    expect((await screen.findByTestId("esign-status")).textContent).toBe(
      "Signed. You can close this page.",
    );
    expect(fetch.mock.calls.map(([path, init]) => [path, JSON.parse(String(init?.body))])).toEqual([
      ["/api/fake-esign/view", { token: "a-signing-token-of-enough-length" }],
      ["/api/fake-esign/sign", { token: "a-signing-token-of-enough-length", name: "Tenant 1" }],
    ]);
  });

  it("shows when the link was signed already", async () => {
    server(json({ ...ENVELOPE, signed: true }));
    render(<ESignPage />);

    expect(await screen.findByTestId("esign-status")).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Sign" })).toBeNull();
  });

  it("refuses another name", async () => {
    server(json(ENVELOPE), json({ detail: "type your name as it is on the lease" }, 422));
    render(<ESignPage />);

    fireEvent.change(await screen.findByLabelText("Type your name as it is on the lease"), {
      target: { value: "Someone Else" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Sign" }));

    expect((await screen.findByRole("alert")).textContent).toBe("type your name as it is on the lease");
    expect(screen.queryByTestId("esign-status")).toBeNull();
  });

  it.each([404, 422])("says a link it doesn't know isn't one (%i)", async (status) => {
    server(json({ detail: "Not Found" }, status));
    render(<ESignPage />);

    expect((await screen.findByRole("alert")).textContent).toBe(
      "This signing link isn't one the partner knows.",
    );
  });

  it("passes on any other trouble", async () => {
    server(json({ detail: "the server is busy" }, 503));
    render(<ESignPage />);

    expect((await screen.findByRole("alert")).textContent).toBe("the server is busy");
  });
});
