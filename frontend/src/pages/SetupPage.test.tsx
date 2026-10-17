import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { Me } from "../api";
import type { Company } from "../company";
import { SetupPage } from "./SetupPage";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

const ME: Me = {
  kind: "staff", email: "staff@example.com", name: "Staff 1", person_id: "s1", pmc_id: "p1", pmc_name: "Synthetic PMC",
};
const ADDRESS = {
  address_line_1: "100 Example Street", address_line_2: "Suite 2", city: "Sampletown", state: "ZZ", postal_code: "00000",
};
const BARE: Company = { name: "Synthetic PMC", address: null, late_fees: null, nsf_fee: null, logo: null };
const FULL: Company = {
  name: "Synthetic PMC", address: ADDRESS,
  late_fees: { grace_days: 5, flat_fee: "50.00", percent: "0", maximum: null }, nsf_fee: "35.00",
  logo: { id: "d1", content_type: "image/png", uploaded_at: "2026-10-17T00:00:00Z" },
};

function server(company: Company, refuse = "") {
  const fetch = vi.fn(async (_path: string, init?: RequestInit) => {
    if (init?.method === "POST") {
      if (refuse) return new Response(JSON.stringify({ detail: refuse }), { status: 422 });
      return new Response(JSON.stringify(company), { status: 200 });
    }
    return new Response(JSON.stringify(company), { status: 200 });
  });
  vi.stubGlobal("fetch", fetch);
  return fetch;
}
const posted = (fetch: ReturnType<typeof server>) =>
  fetch.mock.calls.filter(([, i]) => i?.method === "POST").map(([p, i]) => [p, JSON.parse(String(i?.body))]);
const panel = () => screen.getByRole("dialog");
const type = (label: string, value: string) => fireEvent.change(within(panel()).getByLabelText(label), { target: { value } });

describe("SetupPage", () => {
  it("shows the company's name, address, logo and the fees new properties start with, as one of Settings' pages", async () => {
    window.history.replaceState(null, "", "/settings/setup");
    server(FULL);
    render(<SetupPage me={ME} />);

    const company = await screen.findByLabelText("Company");
    expect(company.textContent).toBe("Synthetic PMC100 Example StreetSuite 2Sampletown, ZZ 00000");
    expect(screen.getByRole("img", { name: "Synthetic PMC logo" }).getAttribute("src")).toBe("/api/company/logo?v=d1");
    expect(screen.getByTestId("default-late-fees").textContent).toBe("After 5 days' grace: $50.00");
    expect(screen.getByTestId("default-nsf-fee").textContent).toBe("NSF fee $35.00");
    expect(screen.getByRole("link", { name: "Settings" }).getAttribute("aria-current")).toBe("page");
  });

  it("says what is not set yet", async () => {
    server(BARE);
    render(<SetupPage me={ME} />);
    expect(await screen.findByText("No mailing address yet")).toBeTruthy();
    expect(screen.getByText("No logo yet")).toBeTruthy();
    expect(screen.getByTestId("default-late-fees").textContent).toBe("No late fee terms");
    expect(screen.getByTestId("default-nsf-fee").textContent).toBe("No NSF fee");
    expect(screen.queryByRole("button", { name: "Remove logo" })).toBeNull();
  });

  it("renames the company and keeps its address in a side panel, the rest as it stands", async () => {
    const fetch = server(FULL);
    render(<SetupPage me={ME} />);
    fireEvent.click(await screen.findByRole("button", { name: "Edit company" }));
    type("Company name", "Synthetic PMC 2");
    type("Suite or floor (optional)", " ");
    fireEvent.click(within(panel()).getByRole("button", { name: "Save" }));
    expect(await screen.findByText("Saved Synthetic PMC 2.")).toBeTruthy();
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(posted(fetch)).toEqual([["/api/company", {
      name: "Synthetic PMC 2", address: { ...ADDRESS, address_line_2: null },
      late_fees: FULL.late_fees, nsf_fee: "35.00",
    }]]);
  });

  it("clears the address when every part of it is emptied", async () => {
    const fetch = server(FULL);
    render(<SetupPage me={ME} />);
    fireEvent.click(await screen.findByRole("button", { name: "Edit company" }));
    for (const label of ["Street address", "Suite or floor (optional)", "City", "State", "ZIP code"]) type(label, "");
    fireEvent.click(within(panel()).getByRole("button", { name: "Save" }));
    await vi.waitFor(() => expect(posted(fetch)).toHaveLength(1));
    expect(posted(fetch)[0]?.[1]).toMatchObject({ address: null });
  });

  it("sets the fees new properties start with, or none", async () => {
    const fetch = server(BARE);
    render(<SetupPage me={ME} />);
    fireEvent.click(await screen.findByRole("button", { name: "Set default fees" }));
    fireEvent.click(within(panel()).getByLabelText("Charge late fees"));
    type("Grace days", "3");
    type("Flat fee", "25.00");
    type("NSF fee (empty for none)", "35.00");
    fireEvent.click(within(panel()).getByRole("button", { name: "Save" }));
    expect(await screen.findByText("Saved the fees new properties start with.")).toBeTruthy();
    expect(posted(fetch)).toEqual([["/api/company", {
      name: "Synthetic PMC", address: null,
      late_fees: { grace_days: 3, flat_fee: "25.00", percent: "0", maximum: null }, nsf_fee: "35.00",
    }]]);
    cleanup();

    const again = server(FULL);
    render(<SetupPage me={ME} />);
    fireEvent.click(await screen.findByRole("button", { name: "Set default fees" }));
    fireEvent.click(within(panel()).getByLabelText("Charge late fees"));
    expect(within(panel()).queryByLabelText("Grace days")).toBeNull();
    type("NSF fee (empty for none)", "");
    fireEvent.click(within(panel()).getByRole("button", { name: "Save" }));
    await vi.waitFor(() => expect(posted(again)).toHaveLength(1));
    expect(posted(again)[0]?.[1]).toMatchObject({ late_fees: null, nsf_fee: null });
  });

  it("uploads a logo, refuses a file that isn't an image, removes it, and says why a save was refused", async () => {
    const fetch = server(FULL);
    render(<SetupPage me={ME} />);
    const input = (await screen.findByLabelText("Replace the logo")) as HTMLInputElement;
    fireEvent.change(input, { target: { files: [new File(["%PDF-1.4"], "logo.pdf", { type: "application/pdf" })] } });
    expect(await screen.findByText("A logo is a PNG or a JPEG.")).toBeTruthy();
    fireEvent.change(input, { target: { files: [new File(["\x89PNG\r\n\x1a\n"], "logo.png", { type: "image/png" })] } });
    expect(await screen.findByText("The logo is saved.")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Remove logo" }));
    expect(await screen.findByText("The logo is removed.")).toBeTruthy();
    expect(posted(fetch).map(([path]) => path)).toEqual(["/api/company/logo", "/api/company/logo/remove"]);
    expect(posted(fetch)[0]?.[1]).toMatchObject({ content_type: "image/png" });
    cleanup();

    server(FULL, "the company name is too long");
    render(<SetupPage me={ME} />);
    fireEvent.click(await screen.findByRole("button", { name: "Edit company" }));
    fireEvent.click(within(panel()).getByRole("button", { name: "Save" }));
    expect((await within(panel()).findByRole("alert")).textContent).toBe("the company name is too long");
  });

  it("says when the company can't be read", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({ detail: "sign in first" }), { status: 401 })));
    render(<SetupPage me={ME} />);
    expect((await screen.findByRole("alert")).textContent).toBe("sign in first");
  });
});
