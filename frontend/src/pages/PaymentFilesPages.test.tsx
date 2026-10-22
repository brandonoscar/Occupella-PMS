import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { Me } from "../api";
import type { FileDetail, FileLine, FileSummary, Preview } from "../paymentFiles";
import type { LeaseDetail, Paged, RentRollRow } from "../rentals";
import { PaymentFilePage, PaymentFilesPage } from "./PaymentFilesPages";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  window.history.pushState(null, "", "/");
});

const ME: Me = {
  kind: "staff",
  email: "staff@example.com",
  name: "Staff 1",
  person_id: "s1",
  pmc_id: "p1",
  pmc_name: "Synthetic PMC",
  roles: [],
};

const LINE: FileLine = {
  id: "line2", line_no: 2, received_on: "2026-10-20", amount: "1200.00", property: "Birch Flats",
  unit: "2B", payer: null, reference: "1043", method: "check", status: "recorded", lease_id: "l1",
  lease: "Morgan Demo, Birch Flats 2B", note: null, by: "Staff 1",
};
const WAITING: FileLine = {
  ...LINE, id: "line3", line_no: 3, amount: "40.00", property: null, unit: null, payer: "Nobody Sample",
  reference: null, status: "waiting", lease_id: null, lease: null,
  note: "no lease in effect on 10/20/2026 at a tenant named Nobody Sample", by: null,
};
const SUMMARY: FileSummary = {
  id: "f1", file_name: "lockbox.csv", came_in: "2026-10-21T14:00:00Z", brought_by: "Staff 1", lines: 2,
  total: "1240.00", recorded: 1, recorded_total: "1200.00", waiting: 1, set_aside: 0,
};
const FILE: FileDetail = { ...SUMMARY, items: [LINE, WAITING] };
const PREVIEW: Preview = {
  lines: [{ ...LINE, id: null, status: "to record" }, { ...WAITING, id: null }],
  to_record: 1, to_record_total: "1200.00", waiting: 1, total: "1240.00", came_in: null,
};
const ROW: RentRollRow = {
  id: "l2", property: { id: "p1", name: "Birch Flats" }, unit: { id: "u1", name: "3C" }, tenants: ["Rowan Demo"],
  people: [{ id: "t1", name: "Rowan Demo" }], starts_on: "2026-01-01", ends_on: null, status: "current",
  days_left: null, monthly_rent: "900.00", deposit: "0.00", owed: "900.00",
};
const LEASE = {
  id: "l2", property: "Birch Flats", unit: "3C", tenants: [{ id: "t1", name: "Rowan Demo" }],
  owners: [{ account: "a1", owner: "Owner 1", percent: null }, { account: "a2", owner: "Owner 2", percent: null }],
  owed: "900.00", on_hold: true,
} as unknown as LeaseDetail;

// GETs answer `answers[path]`; POSTs answer `answers[path]` (201), or `refuse[path]` (409).
function server(answers: Record<string, unknown>, refuse: Record<string, string> = {}) {
  const fetch = vi.fn(async (path: string, init?: RequestInit) => {
    if (path in refuse) return new Response(JSON.stringify({ detail: refuse[path] }), { status: 409 });
    if (!(path in answers)) return new Response(JSON.stringify({ detail: `no ${path}` }), { status: 404 });
    return new Response(JSON.stringify(answers[path]), { status: init?.method === "POST" ? 201 : 200 });
  });
  vi.stubGlobal("fetch", fetch);
  return fetch;
}

const posted = (fetch: ReturnType<typeof server>) =>
  fetch.mock.calls
    .filter(([, init]) => init?.method === "POST")
    .map(([path, init]) => [path, JSON.parse(String(init?.body))]);

const paged = (items: FileSummary[]): Paged<FileSummary> => ({ items, total: items.length, page: 1, per_page: 50 });

function choose(content: string, name = "lockbox.csv") {
  const file = new File([content], name, { type: "text/csv" });
  fireEvent.change(screen.getByLabelText("Payment file (CSV)"), { target: { files: [file] } });
}

describe("PaymentFilesPage", () => {
  it("lists the files that came in, each opening its page", async () => {
    server({ "/api/rent/payment-files?page=1": paged([SUMMARY]) });
    render(<PaymentFilesPage me={ME} />);

    const table = await screen.findByRole("table", { name: "Payment files" });
    const row = within(table).getByRole("row", { name: "lockbox.csv" });
    expect(within(row).getAllByRole("cell").map((c) => c.textContent)).toEqual([
      "10/21/2026", "lockbox.csv", "Staff 1", "2", "1 ($1,200.00)", "1 waiting", "0",
    ]);
    fireEvent.click(row);
    expect(window.location.pathname).toBe("/payment-files/f1");
  });

  it("says when no file has come in", async () => {
    server({ "/api/rent/payment-files?page=1": paged([]) });
    render(<PaymentFilesPage me={ME} />);
    expect(await screen.findByText("No payment file has come in yet.")).toBeTruthy();
  });

  it("shows what a file will do before it comes in, then brings it in and opens it", async () => {
    const content = "Date,Amount,Property,Unit,Payer\n2026-10-20,1200.00,Birch Flats,2B,\n";
    const fetch = server({
      "/api/rent/payment-files?page=1": paged([]),
      "/api/rent/payment-files/preview": PREVIEW,
      "/api/rent/payment-files": FILE,
    });
    render(<PaymentFilesPage me={ME} />);
    await screen.findByText("No payment file has come in yet.");
    fireEvent.click(screen.getByRole("button", { name: "Bring in a file" }));
    choose(content);

    expect((await screen.findByTestId("preview-summary")).textContent).toBe(
      "1 payment ($1,200.00) will be recorded; 1 will wait for you.",
    );
    const table = screen.getByRole("table", { name: "What the file will do" });
    expect(within(table).getAllByRole("row").slice(1).map((r) => r.textContent)).toEqual([
      "210/20/2026$1,200.00Birch Flats 2BTo record Morgan Demo, Birch Flats 2B",
      "310/20/2026$40.00Nobody SampleWaiting no lease in effect on 10/20/2026 at a tenant named Nobody Sample",
    ]);
    fireEvent.click(screen.getByRole("button", { name: "Bring it in" }));

    await vi.waitFor(() => expect(window.location.pathname).toBe("/payment-files/f1"));
    expect(posted(fetch)).toEqual([
      ["/api/rent/payment-files/preview", { file_name: "lockbox.csv", content }],
      ["/api/rent/payment-files", { file_name: "lockbox.csv", content }],
    ]);
  });

  it("won't bring in a file that came in before, and says why a file is refused", async () => {
    server({
      "/api/rent/payment-files?page=1": paged([SUMMARY]),
      "/api/rent/payment-files/preview": { ...PREVIEW, came_in: "2026-10-21T14:00:00Z" },
    });
    render(<PaymentFilesPage me={ME} />);
    await screen.findByRole("table", { name: "Payment files" });
    fireEvent.click(screen.getByRole("button", { name: "Bring in a file" }));
    choose("Date,Amount,Unit\n2026-10-20,1200.00,2B\n");

    expect(await screen.findByText("This file came in on 10/21/2026; it comes in once.")).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Bring it in" })).toBeNull();
    cleanup();

    vi.stubGlobal("fetch", vi.fn(async (path: string) =>
      path === "/api/rent/payment-files/preview"
        ? new Response(JSON.stringify({ detail: "line 1: no unit or payer column" }), { status: 422 })
        : new Response(JSON.stringify(paged([])), { status: 200 })));
    render(<PaymentFilesPage me={ME} />);
    await screen.findByText("No payment file has come in yet.");
    fireEvent.click(screen.getByRole("button", { name: "Bring in a file" }));
    choose("Date,Amount\n2026-10-20,5.00\n");
    expect((await screen.findByRole("alert")).textContent).toBe("line 1: no unit or payer column");
  });
});

describe("PaymentFilePage", () => {
  it("shows each line: recorded to its lease, or waiting with why", async () => {
    server({ "/api/rent/payment-files/f1": FILE });
    render(<PaymentFilePage me={ME} id="f1" />);

    expect((await screen.findByTestId("file-summary")).textContent).toBe(
      "Came in 10/21/2026 from Staff 1. lockbox.csv: 1 payment recorded ($1,200.00); 1 wait for you.",
    );
    const recorded = screen.getByRole("row", { name: "Line 2" });
    expect(within(recorded).getByText("Recorded")).toBeTruthy();
    expect(within(recorded).getByRole("link", { name: "Morgan Demo, Birch Flats 2B" }).getAttribute("href")).toBe("/leases/l1");
    expect(within(recorded).queryByRole("button", { name: /Actions/ })).toBeNull();
    const waiting = screen.getByRole("row", { name: "Line 3" });
    expect(within(waiting).getByText("Waiting")).toBeTruthy();
    expect(within(waiting).getByText(WAITING.note!)).toBeTruthy();
  });

  it("records a waiting line to a lease staff find, its owner's account and past its hold", async () => {
    const fetch = server({
      "/api/rent/payment-files/f1": FILE,
      "/api/leases?q=rowan&show=all&per_page=10": { items: [ROW], total: 1, page: 1, per_page: 10 },
      "/api/leases/l2": LEASE,
      "/api/rent/payment-files/lines/line3/record": { ...WAITING, status: "recorded", lease_id: "l2", lease: "Rowan Demo, Birch Flats 3C", note: null, by: "Staff 1" },
    });
    render(<PaymentFilePage me={ME} id="f1" />);
    const waiting = await screen.findByRole("row", { name: "Line 3" });
    fireEvent.click(within(waiting).getByRole("button", { name: "Actions for line 3" }));
    fireEvent.click(await screen.findByRole("menuitem", { name: "Record to a lease" }));
    const panel = screen.getByRole("dialog", { name: "Record line 3" });
    expect(within(panel).getByText(`Waiting: ${WAITING.note}`)).toBeTruthy();
    fireEvent.change(within(panel).getByLabelText("Lease"), { target: { value: "rowan" } });
    fireEvent.click(await within(panel).findByRole("button", { name: "Birch Flats · 3C · Rowan Demo" }));
    fireEvent.change(await within(panel).findByLabelText("Owner's account"), { target: { value: "a2" } });
    fireEvent.click(within(panel).getByLabelText("Accept this payment past the payment hold"));
    fireEvent.click(within(panel).getByRole("button", { name: "Record $40.00 to this lease" }));

    expect(await screen.findByText("Line 3: $40.00 received 10/20/2026 recorded to Rowan Demo, Birch Flats 3C.")).toBeTruthy();
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(posted(fetch)).toEqual([
      ["/api/rent/payment-files/lines/line3/record", { lease_id: "l2", account: "a2", past_hold: true }],
    ]);
  });

  it("starts from the lease a waiting line names, and says why the books refuse it", async () => {
    const named = { ...WAITING, lease_id: "l2", note: "the lease is on a payment hold: accept the payment past the hold, or set it aside" };
    const hold = named.note;
    server(
      { "/api/rent/payment-files/f1": { ...FILE, items: [named] }, "/api/leases/l2": { ...LEASE, owners: [LEASE.owners[0]] } },
      { "/api/rent/payment-files/lines/line3/record": hold },
    );
    render(<PaymentFilePage me={ME} id="f1" />);
    fireEvent.click(within(await screen.findByRole("row", { name: "Line 3" })).getByRole("button", { name: "Actions for line 3" }));
    fireEvent.click(await screen.findByRole("menuitem", { name: "Record to a lease" }));
    const panel = screen.getByRole("dialog", { name: "Record line 3" });
    expect(await within(panel).findByText("Birch Flats · 3C · Rowan Demo")).toBeTruthy();
    expect(within(panel).queryByLabelText("Owner's account")).toBeNull();
    fireEvent.click(within(panel).getByRole("button", { name: "Record $40.00 to this lease" }));

    expect((await within(panel).findByRole("alert")).textContent).toBe(hold);
    fireEvent.click(within(panel).getByRole("button", { name: "Another lease" }));
    expect(within(panel).getByLabelText("Lease")).toBeTruthy();
  });

  it("sets a waiting line aside with why", async () => {
    const fetch = server({
      "/api/rent/payment-files/f1": FILE,
      "/api/rent/payment-files/lines/line3/set-aside": { ...WAITING, status: "set aside", note: "A utility refund", by: "Staff 1" },
    });
    render(<PaymentFilePage me={ME} id="f1" />);
    fireEvent.click(within(await screen.findByRole("row", { name: "Line 3" })).getByRole("button", { name: "Actions for line 3" }));
    fireEvent.click(await screen.findByRole("menuitem", { name: "Set aside" }));
    const panel = screen.getByRole("dialog", { name: "Set line 3 aside" });
    fireEvent.change(within(panel).getByLabelText("Why"), { target: { value: "A utility refund" } });
    fireEvent.click(within(panel).getByRole("button", { name: "Set it aside" }));

    expect(await screen.findByText("Line 3 is set aside; it records nothing.")).toBeTruthy();
    expect(posted(fetch)).toEqual([
      ["/api/rent/payment-files/lines/line3/set-aside", { reason: "A utility refund" }],
    ]);
  });

  it("says when the file can't be read", async () => {
    server({});
    render(<PaymentFilePage me={ME} id="f9" />);
    expect((await screen.findByRole("alert")).textContent).toBe("no /api/rent/payment-files/f9");
  });
});
