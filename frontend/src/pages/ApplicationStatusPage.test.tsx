import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ApplicationStatusPage } from "./ApplicationStatusPage";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  window.history.replaceState(null, "", "/");
  sessionStorage.clear();
});

const RECEIVED = {
  property: "Property 1", unit: "Unit 1", submitted_at: "2026-10-11T18:05:00Z", status: "received", conditions: null,
};

describe("ApplicationStatusPage", () => {
  it("shows where the application stands, with any conditions", async () => {
    window.history.replaceState(null, "", "/application#status-token");
    const fetch = vi.fn(async (_path: string, _init?: RequestInit) =>
      new Response(
        JSON.stringify({
          property: "Property 1",
          unit: "Unit 1",
          submitted_at: "2026-10-11T18:05:00Z",
          status: "conditional",
          conditions: "One more month's deposit",
        }),
      ),
    );
    vi.stubGlobal("fetch", fetch);
    render(<ApplicationStatusPage />);

    expect((await screen.findByTestId("application-status")).textContent).toBe(
      "Approved, on conditions:",
    );
    expect(screen.getByText("One more month's deposit")).toBeTruthy();
    expect(screen.getByText("Sent 10/11/2026 18:05 UTC")).toBeTruthy();
    expect(JSON.parse(String(fetch.mock.calls[0]?.[1]?.body))).toEqual({
      status_token: "status-token",
    });
    // No notice came with it, so none shows.
    expect(screen.queryByTestId("adverse-action-notice")).toBeNull();
  });

  it("gives a declined applicant the adverse action notice", async () => {
    window.history.replaceState(null, "", "/application#status-token");
    const notice = "Your application was declined.\n\nWhy:\n- Income too low\n";
    vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({
      property: "Property 1", unit: "Unit 1", submitted_at: "2026-10-11T18:05:00Z", status: "declined",
      conditions: null, notice,
    }))));
    render(<ApplicationStatusPage />);

    expect((await screen.findByTestId("adverse-action-notice")).textContent).toBe(notice);
    expect(screen.getByRole("heading", { name: "Adverse action notice" })).toBeTruthy();
  });

  it("lets the applicant pay the application fee on the processor's page, keeping the link in the tab", async () => {
    window.history.replaceState(null, "", "/application#status-token");
    const fetch = vi.fn(async (path: string, _init?: RequestInit) =>
      path === "/api/application/fee"
        ? new Response(JSON.stringify({ url: "/pay#fake_fee_abc" }), { status: 201 })
        : new Response(JSON.stringify({
          ...RECEIVED, fee: { due: "90.00", paid: null, methods: ["card", "bank debit"] },
        })),
    );
    vi.stubGlobal("fetch", fetch);
    const go = vi.fn();
    render(<ApplicationStatusPage go={go} />);

    expect((await screen.findByTestId("application-fee")).textContent).toBe("$90.00 to pay");
    fireEvent.click(screen.getByRole("button", { name: "Pay from a bank account" }));
    await vi.waitFor(() => expect(go).toHaveBeenCalledWith("/pay#fake_fee_abc"));
    expect(JSON.parse(String(fetch.mock.calls[1]?.[1]?.body))).toEqual({
      status_token: "status-token", method: "bank debit",
    });
    expect(sessionStorage.getItem("pms.applicationStatus")).toBe("status-token");
    expect(screen.getByRole("button", { name: "Pay by card" })).toBeTruthy();
  });

  it("comes back from the processor without the token in the link and says the payment is on its way", async () => {
    sessionStorage.setItem("pms.applicationStatus", "status-token");
    window.history.replaceState(null, "", "/application?paid=f1");
    const fetch = vi.fn(async (_path: string, _init?: RequestInit) => new Response(JSON.stringify({
      ...RECEIVED, fee: { due: "90.00", paid: null, methods: ["card"] },
    })));
    vi.stubGlobal("fetch", fetch);
    render(<ApplicationStatusPage go={vi.fn()} />);

    expect(await screen.findByText(/Your payment is on its way/)).toBeTruthy();
    expect(JSON.parse(String(fetch.mock.calls[0]?.[1]?.body))).toEqual({ status_token: "status-token" });
    // The link the applicant keeps is back in the address bar.
    expect(window.location.hash).toBe("#status-token");
  });

  it("says a fee paid is paid, and one the company doesn't take online is asked of them", async () => {
    window.history.replaceState(null, "", "/application#status-token");
    vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({
      ...RECEIVED, fee: { due: null, paid: "90.00", methods: [] },
    }))));
    render(<ApplicationStatusPage go={vi.fn()} />);
    expect((await screen.findByTestId("application-fee")).textContent).toBe("Paid $90.00. Thank you.");
    expect(screen.queryByRole("button")).toBeNull();
    cleanup();

    vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({
      ...RECEIVED, fee: { due: "45.00", paid: null, methods: [] },
    }))));
    render(<ApplicationStatusPage go={vi.fn()} />);
    expect((await screen.findByTestId("application-fee")).textContent).toBe("$45.00 to pay");
    expect(screen.getByText(/doesn't take it online yet/)).toBeTruthy();
    cleanup();

    // No fee asked: no fee part.
    vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({
      ...RECEIVED, fee: { due: null, paid: null, methods: [] },
    }))));
    render(<ApplicationStatusPage go={vi.fn()} />);
    expect(await screen.findByTestId("application-status")).toBeTruthy();
    expect(screen.queryByTestId("application-fee")).toBeNull();
  });

  it("shows the server's reason when paying is refused", async () => {
    window.history.replaceState(null, "", "/application#status-token");
    vi.stubGlobal("fetch", vi.fn(async (path: string) =>
      path === "/api/application/fee"
        ? new Response(JSON.stringify({ detail: "this application has no fee to pay" }), { status: 409 })
        : new Response(JSON.stringify({ ...RECEIVED, fee: { due: "45.00", paid: null, methods: ["card"] } }))));
    const go = vi.fn();
    render(<ApplicationStatusPage go={go} />);
    fireEvent.click(await screen.findByRole("button", { name: "Pay by card" }));
    expect((await screen.findByRole("alert")).textContent).toBe("this application has no fee to pay");
    expect(go).not.toHaveBeenCalled();
  });
});
