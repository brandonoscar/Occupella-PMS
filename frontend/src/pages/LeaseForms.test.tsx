import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { Paid, RentLease } from "../rent";
import { PaymentForm, PaymentRow } from "./LeaseForms";

afterEach(cleanup);

// Receive payment's form (ADR 0010 phase 1c): a note and a check's photo kept with the payment,
// the open charges it pays first, and Save and add another.
const LEASE = {
  id: "l1",
  property: "Property 1",
  unit: "Unit 1",
  tenants: [{ id: "t1", name: "Tenant 1", prepaid: "0.00", deposit: "0.00", deposits: [] }],
  owners: [{ account: "a1", owner: "Owner 1", percent: null }],
  owed: "1550.00",
  moved_from: null,
  on_hold: false,
  open_charges: [
    { id: "c1", due_on: "2026-10-01", kind: "rent", memo: "Rent due 2026-10-01", amount: "1500.00", open: "1500.00" },
    { id: "c2", due_on: "2026-10-06", kind: "fee", memo: "Late fee", amount: "50.00", open: "50.00" },
  ],
} as unknown as RentLease;

type Body = Record<string, unknown>;

function recorder() {
  return vi.fn(async (_path: string, _body: object, _done: (answer: never) => string) => true);
}

describe("PaymentForm", () => {
  it("sends the note, the check's photo and the charges chosen with the payment, then starts the next", async () => {
    const record = recorder();
    const onRecorded = vi.fn();
    render(<PaymentForm lease={LEASE} record={record} onRecorded={onRecorded} />);
    fireEvent.change(screen.getByLabelText("Payment received"), { target: { value: "60.00" } });
    fireEvent.change(screen.getByLabelText("It pays"), { target: { value: "chosen" } });
    const choices = screen.getByRole("group", { name: "Charges it pays first" });
    expect(within(choices).getByText("Choose the charges it pays first. What is left of it pays the rest as usual.")).toBeTruthy();
    fireEvent.click(within(choices).getByLabelText("10/06/2026 Late fee: $50.00 open"));
    const part = within(choices).getByLabelText("Pays of 10/06/2026 Late fee: $50.00 open") as HTMLInputElement;
    expect(part.value).toBe("50.00");
    fireEvent.change(part, { target: { value: "40.00" } });
    expect(within(choices).getByText("$40.00 to the charges chosen; what is left of it pays the rest as usual.")).toBeTruthy();
    // Its line says what it pays first, where the rest goes and who is emailed (ADR 0010, rule 1).
    const pay = screen.getByRole("button", { name: "Record payment" });
    expect(document.getElementById(pay.getAttribute("aria-describedby") ?? "")?.textContent).toBe(
      "Pays the charges chosen first, then the rest owed; any extra is held as prepaid rent."
        + " Each tenant with an email on file is emailed a receipt.",
    );
    // A part typed half way counts nothing yet, and nothing breaks.
    fireEvent.change(part, { target: { value: "40." } });
    expect(within(choices).getByText("$0.00 to the charges chosen; what is left of it pays the rest as usual.")).toBeTruthy();
    fireEvent.change(part, { target: { value: "40.00" } });
    fireEvent.change(screen.getByLabelText("Note (staff only)"), { target: { value: " Dropped off at the office " } });
    const photo = new File(["synthetic photo"], "check 1042.png", { type: "image/png" });
    fireEvent.change(screen.getByLabelText("Check photo or PDF"), { target: { files: [photo] } });
    // The file is read before the button is pressed: wait for its bytes.
    await new Promise((resolve) => setTimeout(resolve, 0));
    fireEvent.click(screen.getByRole("button", { name: "Save and add another" }));

    await vi.waitFor(() => expect(onRecorded).toHaveBeenCalledWith(true));
    const [path, body] = record.mock.calls[0] as unknown as [string, Body];
    expect(path).toBe("/api/rent/leases/l1/payments");
    expect(body).toMatchObject({
      amount: "60.00",
      method: "check",
      note: "Dropped off at the office",
      charges: [{ charge_id: "c2", amount: "40.00" }],
      attachment: { title: "check 1042", content_type: "image/png", content_base64: btoa("synthetic photo") },
    });
    // Cleared for the next one, under a new request id.
    expect((screen.getByLabelText("Payment received") as HTMLInputElement).value).toBe("");
    expect((screen.getByLabelText("Note (staff only)") as HTMLTextAreaElement).value).toBe("");
    expect(screen.queryByRole("group", { name: "Charges it pays first" })).toBeNull();
    fireEvent.change(screen.getByLabelText("Payment received"), { target: { value: "5.00" } });
    fireEvent.click(screen.getByRole("button", { name: "Record payment" }));
    await vi.waitFor(() => expect(onRecorded).toHaveBeenLastCalledWith(false));
    const second = record.mock.calls[1]?.[1] as Body;
    expect(second.request_id).not.toBe(body.request_id);
    expect(second).not.toHaveProperty("note");
    expect(second).not.toHaveProperty("attachment");
    expect(second).not.toHaveProperty("charges");
  });

  it("pays what is owed, rent first and oldest first, unless staff choose", async () => {
    const record = recorder();
    render(<PaymentForm lease={LEASE} record={record} />);
    expect((screen.getByLabelText("It pays") as HTMLSelectElement).value).toBe("oldest");
    // With nowhere to start another, only Record payment.
    expect(screen.queryByRole("button", { name: "Save and add another" })).toBeNull();
    fireEvent.change(screen.getByLabelText("It pays"), { target: { value: "chosen" } });
    fireEvent.change(screen.getByLabelText("It pays"), { target: { value: "oldest" } });
    fireEvent.change(screen.getByLabelText("Payment received"), { target: { value: "100.00" } });
    fireEvent.click(screen.getByRole("button", { name: "Record payment" }));

    await vi.waitFor(() => expect(record).toHaveBeenCalled());
    expect(record.mock.calls[0]?.[1]).not.toHaveProperty("charges");
  });

  it("refuses a file that isn't a PDF, a JPEG or a PNG before sending anything", async () => {
    const record = recorder();
    render(<PaymentForm lease={LEASE} record={record} onRecorded={vi.fn()} />);
    const text = new File(["hello"], "notes.txt", { type: "text/plain" });
    fireEvent.change(screen.getByLabelText("Check photo or PDF"), { target: { files: [text] } });

    expect((await screen.findByRole("alert")).textContent).toBe("A file is a PDF, a JPEG or a PNG.");
    expect(screen.getByRole("button", { name: "Record payment" })).toHaveProperty("disabled", true);
    expect(screen.getByRole("button", { name: "Save and add another" })).toHaveProperty("disabled", true);
    expect(record).not.toHaveBeenCalled();
  });

  it("offers no choice of charges when nothing is open, or the lease's tenants moved to it", () => {
    render(<PaymentForm lease={{ ...LEASE, open_charges: [] }} record={recorder()} />);
    expect(screen.queryByLabelText("It pays")).toBeNull();
    cleanup();
    const moved = { ...LEASE, moved_from: { lease_id: "l0", home: "Property 1 · Unit 9", moved_on: "2026-09-30" } } as unknown as RentLease;
    render(<PaymentForm lease={moved} record={recorder()} />);
    expect(screen.queryByLabelText("It pays")).toBeNull();
  });
});

describe("PaymentRow", () => {
  const PAID: Paid = {
    transfer_id: "tr1", received_on: "2026-10-02", amount: "1500.00", reference: "Check 1042", method: "check",
    bounced: false, note: "Dropped off at the office", attachments: ["d1"],
  };

  it("shows what staff wrote about a payment and its file beside the receipt", () => {
    render(<table><tbody><PaymentRow payment={PAID} record={recorder()} /></tbody></table>);
    expect(screen.getByText("Note: Dropped off at the office")).toBeTruthy();
    expect(screen.getByRole("link", { name: "Check photo or PDF" }).getAttribute("href")).toBe("/api/documents/d1/file");
    expect(screen.getByRole("link", { name: "Receipt (PDF)" }).getAttribute("href")).toBe("/api/rent/payments/tr1/receipt");
  });

  it("shows neither for a payment without them", () => {
    render(<table><tbody><PaymentRow payment={{ ...PAID, note: null, attachments: [] }} record={recorder()} /></tbody></table>);
    expect(screen.queryByText(/^Note:/)).toBeNull();
    expect(screen.queryByRole("link", { name: "Check photo or PDF" })).toBeNull();
  });
});
