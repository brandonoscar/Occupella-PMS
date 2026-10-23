import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { Autopay } from "../onlinePayments";
import { rentRunSummary } from "../rent";
import { AutopayCard, readView } from "./AutopayCard";
import { FakeCheckoutPage } from "./FakeCheckoutPage";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  window.history.replaceState(null, "", "/");
});

const ON: Autopay = {
  id: "a1", lease_id: "l1", home: "Elm Court, 1A", tenant: "Taylor Demo", method: "card",
  since: "2026-10-22T10:00:00Z", status: "on", label: "Card ••••4242", reason: null,
};

function server(start: { methods: string[]; autopay: Autopay[] }) {
  let now = start;
  const fetch = vi.fn(async (path: string, init?: RequestInit) => {
    if (init?.method !== "POST") return new Response(JSON.stringify(now), { status: 200 });
    if (path.endsWith("/stop")) {
      now = { ...now, autopay: now.autopay.map((a) => ({ ...a, status: "stopped" as const, reason: "the tenant stopped it" })) };
      return new Response(null, { status: 204 });
    }
    return new Response(JSON.stringify({ autopay_id: "a2", url: "/pay#fake_setup_x" }), { status: 201 });
  });
  vi.stubGlobal("fetch", fetch);
  return fetch;
}

const posts = (fetch: ReturnType<typeof server>) =>
  fetch.mock.calls.filter(([, init]) => init?.method === "POST").map(([path, init]) => [path, JSON.parse(String(init?.body))]);

describe("AutopayCard", () => {
  it("sets autopay up on the processor's page", async () => {
    const fetch = server({ methods: ["card", "bank debit"], autopay: [] });
    const go = vi.fn();
    render(<AutopayCard leaseId="l1" go={go} />);

    fireEvent.change(await screen.findByLabelText("Pay automatically by"), { target: { value: "bank debit" } });
    fireEvent.click(screen.getByRole("button", { name: "Set up autopay" }));

    await vi.waitFor(() => expect(go).toHaveBeenCalledWith("/pay#fake_setup_x"));
    expect(posts(fetch)).toEqual([["/api/tenant/leases/l1/autopay", { method: "bank debit" }]]);
  });

  it("says it is on from what was saved and stops after a question", async () => {
    const fetch = server({ methods: ["card"], autopay: [ON, { ...ON, id: "a0", lease_id: "l9" }] });
    render(<AutopayCard leaseId="l1" go={vi.fn()} />);

    expect((await screen.findByTestId("autopay")).textContent).toBe(
      "On: Card ••••4242. What the lease owes is paid from it once rent is due each month.",
    );
    // This lease's autopay only.
    expect(within(screen.getByRole("list", { name: "Autopay history" })).getAllByRole("listitem")).toHaveLength(1);
    fireEvent.click(screen.getByRole("button", { name: "Stop autopay" }));
    const asked = screen.getByRole("alertdialog");
    expect(asked.textContent).toContain("Stop autopay from Card ••••4242?");
    fireEvent.click(within(asked).getByRole("button", { name: "Stop autopay" }));

    expect(await screen.findByText("Autopay is stopped.")).toBeTruthy();
    expect(posts(fetch)).toEqual([["/api/tenant/autopay/a1/stop", {}]]);
    expect(await screen.findByText("the tenant stopped it")).toBeTruthy();
    // Stopped, it may be set up again.
    expect(screen.getByRole("button", { name: "Set up autopay" })).toBeTruthy();
  });

  it("shows nothing when nothing is offered, and reads another answer as nothing", async () => {
    server({ methods: [], autopay: [] });
    const { container } = render(<AutopayCard leaseId="l1" go={vi.fn()} />);
    await vi.waitFor(() => expect(container.textContent).toBe(""));
    expect(readView({ name: "not a view" })).toEqual({ methods: [], autopay: [] });
  });

  it("says in the run's summary how many payments autopay started", () => {
    expect(rentRunSummary({ rents: 2, recurring: 0, late_fees: 0, autopay: 1 })).toBe(
      "2 rents, 0 recurring charges and 0 late fees charged. Autopay started 1 payment.",
    );
  });
});

describe("FakeCheckoutPage saving a card", () => {
  it("says the card was saved and goes back home", async () => {
    window.history.replaceState(null, "", "/pay#fake_setup_abc");
    const fetch = vi.fn(async (_path: string, _init?: RequestInit) => new Response(null, { status: 204 }));
    vi.stubGlobal("fetch", fetch);
    render(<FakeCheckoutPage me={{ kind: "tenant", name: "Taylor Demo", pmc_name: "Demo PMC" } as never} />);

    expect(screen.getByRole("heading", { name: "Your autopay" })).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    await vi.waitFor(() => expect(window.location.pathname).toBe("/"));
    expect(fetch.mock.calls.map(([path, init]) => [path, JSON.parse(String(init?.body))])).toEqual([
      ["/api/tenant/autopay/fake/fake_setup_abc", { outcome: "saved" }],
    ]);
  });
});
