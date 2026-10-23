import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { PersonTexts } from "../texts";
import { consentLine, shownPhone } from "../texts";
import { TextsCard } from "./TextsCard";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

const NONE: PersonTexts = { sending: true, consent: null, phone_on_file: "+12025550123", texts: [] };
const AGREED: PersonTexts = {
  sending: true,
  consent: { phone: "+12025550123", consented: true, how: "in writing", at: "2026-10-23T15:00:00Z", by: "Writer 1" },
  phone_on_file: "+12025550123",
  texts: [{
    id: "t1", kind: "rent_reminder", to: "+12025550123", body: "Synthetic PMC: rent is due. Reply STOP to stop.",
    at: "2026-10-23T16:00:00Z", by: null, status: "failed", error: "Twilio said 400: invalid",
  }],
};

function server(views: PersonTexts[], refuse: Record<string, string> = {}) {
  let at = 0;
  const fetch = vi.fn(async (path: string, init?: RequestInit) => {
    if (init?.method === "POST") {
      const where = path.split("?")[0] ?? path;
      if (where in refuse) return new Response(JSON.stringify({ detail: refuse[where] }), { status: 409 });
      at = Math.min(at + 1, views.length - 1);
      return new Response(JSON.stringify(path === "/api/texts" ? { id: "t2", to: "+12025550123" } : views[at]), { status: 201 });
    }
    return new Response(JSON.stringify(views[at]));
  });
  vi.stubGlobal("fetch", fetch);
  return fetch;
}

const posted = (fetch: ReturnType<typeof server>) =>
  fetch.mock.calls.filter(([, init]) => init?.method === "POST").map(([path, init]) => [path, JSON.parse(String(init?.body))]);

describe("TextsCard", () => {
  it("records the word a person gave staff, starting from the phone on file", async () => {
    const fetch = server([NONE, AGREED]);
    render(<TextsCard about="tenant" aboutId="p1" name="Tenant A" />);

    expect((await screen.findByTestId("text-consent")).textContent).toBe("No word on texts yet");
    expect(screen.queryByRole("button", { name: "Write text" })).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Record their word" }));
    const panel = screen.getByRole("dialog", { name: "Tenant A's word on texts" });
    expect((within(panel).getByLabelText("Mobile number") as HTMLInputElement).value).toBe("(202) 555-0123");
    fireEvent.change(within(panel).getByLabelText("How they gave it"), { target: { value: "verbally" } });
    fireEvent.click(within(panel).getByRole("button", { name: "Save" }));

    expect(await screen.findByText("Tenant A agreed to texts.")).toBeTruthy();
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(posted(fetch)).toEqual([["/api/texts/consent", {
      about: "tenant", about_id: "p1", phone: "(202) 555-0123", consented: true, how: "verbally",
    }]]);
    await vi.waitFor(() => expect(screen.getByTestId("text-consent").textContent).toBe(
      "Agreed to texts at (202) 555-0123 in writing, 10/23/2026 (recorded by Writer 1)",
    ));
    const line = within(screen.getByRole("list", { name: "Texts, Tenant A" })).getByRole("listitem");
    expect(line.textContent).toContain("Rent reminder to (202) 555-0123 · Sent by the PMS · 10/23/2026");
    expect(line.textContent).toContain("Not sent");
    expect(line.textContent).toContain("Twilio said 400: invalid");
  });

  it("writes a text to someone who agreed, counting what is left, and shows a refusal", async () => {
    const fetch = server([AGREED], { "/api/texts": "Tenant A hasn't agreed to texts: record their word first" });
    render(<TextsCard about="owner" aboutId="o1" name="Tenant A" />);
    fireEvent.click(await screen.findByRole("button", { name: "Write text" }));
    const panel = screen.getByRole("dialog", { name: "Text Tenant A" });
    expect(within(panel).getByText(/^To \(202\) 555-0123\./)).toBeTruthy();
    fireEvent.change(within(panel).getByLabelText("Text"), { target: { value: " The gate is fixed. " } });
    expect(within(panel).getByText("280 characters left")).toBeTruthy();
    fireEvent.click(within(panel).getByRole("button", { name: "Send text" }));
    expect((await within(panel).findByRole("alert")).textContent).toContain("hasn't agreed to texts");
    expect(posted(fetch)).toEqual([["/api/texts", { about: "owner", about_id: "o1", text: "The gate is fixed." }]]);
  });

  it("sends a text and says where it went", async () => {
    server([AGREED]);
    render(<TextsCard about="vendor" aboutId="v1" name="Vendor A" />);
    fireEvent.click(await screen.findByRole("button", { name: "Write text" }));
    fireEvent.change(screen.getByLabelText("Text"), { target: { value: "See you at 9." } });
    fireEvent.click(screen.getByRole("button", { name: "Send text" }));
    expect(await screen.findByText("Text sent to (202) 555-0123.")).toBeTruthy();
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("says when the server sends no texts, and offers no Write text then", async () => {
    server([{ ...AGREED, sending: false, texts: [] }]);
    render(<TextsCard about="tenant" aboutId="p1" name="Tenant A" />);
    expect(await screen.findByText(/This server sends no texts yet/)).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Write text" })).toBeNull();
    expect(screen.getByText("No text to Tenant A yet.")).toBeTruthy();
  });

  it("words each way a person may have given their word", () => {
    expect(shownPhone("+19995550199")).toBe("(999) 555-0199");
    const at = "2026-10-23T15:00:00Z";
    expect(consentLine({ phone: "+12025550123", consented: false, how: "stop reply", at, by: null }))
      .toBe("Said no to texts at (202) 555-0123 by replying STOP, 10/23/2026");
    expect(consentLine({ phone: "+12025550123", consented: true, how: "portal", at, by: null }))
      .toBe("Agreed to texts at (202) 555-0123 in the portal, 10/23/2026");
  });
});
