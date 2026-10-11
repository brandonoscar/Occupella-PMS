import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiError, api } from "./api";

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

  it("says what to do about input the server couldn't read", async () => {
    vi.stubGlobal("fetch", answer(422, { detail: [{ loc: ["body", "email"] }] }));

    await expect(api.post("/api/sign-in")).rejects.toMatchObject({
      detail: "check what you entered and try again",
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
});
