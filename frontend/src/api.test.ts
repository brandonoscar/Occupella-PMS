import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiError, api, saveFile } from "./api";

function answer(status: number, body: unknown) {
  return vi.fn().mockResolvedValue(
    new Response(body === undefined ? null : JSON.stringify(body), { status }),
  );
}

afterEach(() => vi.unstubAllGlobals());

describe("api", () => {
  it("sends changes as JSON with the session cookie", async () => {
    const fetch = answer(200, { ok: true });
    vi.stubGlobal("fetch", fetch);

    await expect(api.post("/api/sign-in", { email: "a@example.com" })).resolves.toEqual({ ok: true });

    expect(fetch).toHaveBeenCalledWith("/api/sign-in", {
      method: "POST",
      credentials: "same-origin",
      headers: { "Content-Type": "application/json" },
      body: '{"email":"a@example.com"}',
    });
  });

  it("turns a refusal into an ApiError with the server's reason", async () => {
    vi.stubGlobal("fetch", answer(401, { detail: "the email or password is wrong" }));

    const refused = await api.get("/api/me").catch((e: unknown) => e);

    expect(refused).toBeInstanceOf(ApiError);
    expect(refused).toMatchObject({ status: 401, detail: "the email or password is wrong" });
  });

  it("keeps the whole refusal, for what it gives the page besides its words", async () => {
    const body = { detail: "not enough money to record the bounce", short_owners: [{ owner_id: "o1", owner: "Owner 1" }] };
    vi.stubGlobal("fetch", answer(409, body));

    await expect(api.post("/api/rent/payments/t1/bounce", { on: "2026-10-20" })).rejects.toMatchObject({
      status: 409,
      detail: "not enough money to record the bounce",
      body,
    });
  });

  it("says what to do about input the server couldn't read", async () => {
    vi.stubGlobal("fetch", answer(422, { detail: [{ loc: ["body", "email"] }] }));

    await expect(api.post("/api/sign-in")).rejects.toMatchObject({
      detail: "check what you entered and try again",
    });
  });

  it("says a rule of the PMS's own in its words", async () => {
    vi.stubGlobal("fetch", answer(422, {
      detail: [{ loc: ["body"], msg: "Value error, a mailing address needs its street, city, state and ZIP code" }],
    }));

    await expect(api.post("/api/tenant/contact")).rejects.toMatchObject({
      detail: "a mailing address needs its street, city, state and ZIP code",
    });
  });

  it("names the status when the answer has no reason", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response("oops", { status: 502 })));

    await expect(api.get("/api/me")).rejects.toMatchObject({ detail: "the server answered 502" });
  });

  it("returns nothing for 204", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(null, { status: 204 })));

    await expect(api.post("/api/sign-out")).resolves.toBeUndefined();
  });

  it("saves a file a change answers with, under the name given", async () => {
    const fetch = vi.fn().mockResolvedValue(new Response("%PDF-1.4", { status: 200 }));
    vi.stubGlobal("fetch", fetch);
    const made = vi.fn(() => "blob:lease");
    vi.stubGlobal("URL", { createObjectURL: made, revokeObjectURL: vi.fn() });
    const clicked: string[] = [];
    const click = vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(function (this: HTMLAnchorElement) {
      clicked.push(`${this.download} ${this.getAttribute("href")}`);
    });

    await saveFile("/api/signing/document", { token: "t" }, "lease.pdf");

    expect(fetch).toHaveBeenCalledWith("/api/signing/document", {
      method: "POST",
      credentials: "same-origin",
      headers: { "Content-Type": "application/json" },
      body: '{"token":"t"}',
    });
    expect(clicked).toEqual(["lease.pdf blob:lease"]);
    expect(document.querySelector("a[download]")).toBeNull();
    click.mockRestore();
  });

  it("refuses to save a file the server refused, with its reason", async () => {
    vi.stubGlobal("fetch", answer(409, { detail: "agree to sign electronically first, or ask for paper" }));

    await expect(saveFile("/api/signing/document", { token: "t" }, "lease.pdf")).rejects.toMatchObject({
      status: 409,
      detail: "agree to sign electronically first, or ask for paper",
    });
  });

  it("names the status when a refused file has no reason", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response("oops", { status: 500 })));

    await expect(saveFile("/api/signing/certificate", {}, "c.pdf")).rejects.toMatchObject({
      detail: "the server answered 500",
    });
  });
});
