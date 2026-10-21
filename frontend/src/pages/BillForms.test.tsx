import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { Portfolio } from "../setup";
import { RecordBillForm, linesBody } from "./BillForms";

afterEach(() => {
  cleanup();
});

const BOOKS = {
  bank_accounts: [], owners: [], properties: [], tenants: [],
  accounts: [{ account: "a1", owner_id: "o1", owner: "Owner 1", property_id: "pr1", property: "Property 1", bank_account: "Operating", agreement: null }],
  vendors: [{ id: "v1", name: "Vendor 1" }],
  expense_accounts: [{ id: "e1", name: "Repairs", schedule_e_line: 14 }],
  schedule_e_lines: [{ line: 14, label: "Repairs" }],
} as unknown as Portfolio;

function fill() {
  fireEvent.change(screen.getByLabelText("Ref #"), { target: { value: "INV-9" } });
  fireEvent.change(screen.getByLabelText("Bill date"), { target: { value: "2026-10-01" } });
  fireEvent.change(screen.getByLabelText("Due"), { target: { value: "2026-10-10" } });
  fireEvent.change(screen.getByLabelText("Amount"), { target: { value: "75.00" } });
  fireEvent.change(screen.getByLabelText("Memo"), { target: { value: "Hose" } });
}

describe("RecordBillForm", () => {
  it("sends the vendor's invoice with the bill, in one expense account", async () => {
    const record = vi.fn(async () => true);
    render(<RecordBillForm books={BOOKS} record={record} />);
    fill();
    fireEvent.change(screen.getByLabelText("Expense account"), { target: { value: "e1" } });
    const pdf = new File(["%PDF-1.4 synthetic"], "invoice 9.pdf", { type: "application/pdf" });
    fireEvent.change(screen.getByLabelText("Invoice (PDF, JPEG or PNG)"), { target: { files: [pdf] } });
    await vi.waitFor(() => expect(screen.getByRole("button", { name: "Record bill" })).toHaveProperty("disabled", false));
    // The file is read before the button is pressed: wait for its bytes.
    await new Promise((resolve) => setTimeout(resolve, 0));
    fireEvent.click(screen.getByRole("button", { name: "Record bill" }));

    await vi.waitFor(() => expect(record).toHaveBeenCalled());
    const [path, body] = record.mock.calls[0] as unknown as [string, Record<string, unknown>];
    expect(path).toBe("/api/payables/bills");
    expect(body).toMatchObject({
      vendor_id: "v1", account: "a1", reference: "INV-9", amount: "75.00", expense_account_id: "e1",
      invoice: { title: "invoice 9", content_type: "application/pdf", content_base64: btoa("%PDF-1.4 synthetic") },
    });
  });

  it("refuses an invoice that isn't a PDF, a JPEG or a PNG before sending anything", async () => {
    const record = vi.fn(async () => true);
    render(<RecordBillForm books={BOOKS} record={record} />);
    fill();
    const text = new File(["hello"], "notes.txt", { type: "text/plain" });
    fireEvent.change(screen.getByLabelText("Invoice (PDF, JPEG or PNG)"), { target: { files: [text] } });

    expect((await screen.findByRole("alert")).textContent).toBe("An invoice is a PDF, a JPEG or a PNG.");
    expect(screen.getByRole("button", { name: "Record bill" })).toHaveProperty("disabled", true);
    expect(record).not.toHaveBeenCalled();
  });

  it("asks for a vendor and an owner's property first", () => {
    render(<RecordBillForm books={{ ...BOOKS, vendors: [] }} record={vi.fn()} />);
    expect(screen.getByText("Add a vendor from Vendors and a property with an owner from Properties first.")).toBeTruthy();
  });
});

const TERMS = {
  starts_on: "2026-01-01", fee_percent: "8", minimum_fee: "0", flat_fee: "0", leasing_fee_percent: "0",
  reserve: "0", approval_limit: "500.00",
};
const TWO = {
  ...BOOKS,
  accounts: [
    { account: "a1", owner_id: "o1", owner: "Owner 1", property_id: "pr1", property: "Property 1", bank_account: "Operating",
      agreement: { ...TERMS, bill_markup_percent: "10" } },
    { account: "a2", owner_id: "o2", owner: "Owner 2", property_id: "pr2", property: "Property 2", bank_account: "Operating",
      agreement: { ...TERMS, bill_markup_percent: "0" } },
  ],
} as unknown as Portfolio;

function fillShared(total: string) {
  fireEvent.click(screen.getByLabelText("Share across properties"));
  fireEvent.change(screen.getByLabelText("Ref #"), { target: { value: "ROOF-1" } });
  fireEvent.change(screen.getByLabelText("Bill date"), { target: { value: "2026-10-01" } });
  fireEvent.change(screen.getByLabelText("Due"), { target: { value: "2026-10-10" } });
  fireEvent.change(screen.getByLabelText("Invoice total"), { target: { value: total } });
  fireEvent.change(screen.getByLabelText("Memo"), { target: { value: "Roof" } });
}

describe("RecordBillForm shared across properties, and the markup", () => {
  it("shares one invoice across properties, split evenly, with each owner's markup", async () => {
    const record = vi.fn(async () => true);
    render(<RecordBillForm books={TWO} record={record} />);
    fillShared("900.01");
    // One owner per share: the single owner's choice is gone.
    expect(screen.queryByLabelText("Owner and property")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Split evenly" }));
    expect((screen.getByLabelText("Amount, share 1") as HTMLInputElement).value).toBe("450.01");
    expect((screen.getByLabelText("Amount, share 2") as HTMLInputElement).value).toBe("450.00");
    expect(screen.getByTestId("shares-total").textContent).toBe("Shares come to $900.01 of $900.01.");
    fireEvent.click(screen.getByLabelText("Add each owner's markup, as their terms set"));
    fireEvent.click(screen.getByRole("button", { name: "Record bill" }));

    await vi.waitFor(() => expect(record).toHaveBeenCalled());
    const [path, body, done] = record.mock.calls[0] as unknown as [string, Record<string, unknown>, () => string];
    expect(path).toBe("/api/payables/shared-bills");
    expect(body).toEqual({
      vendor_id: "v1", reference: "ROOF-1", bill_date: "2026-10-01", due_on: "2026-10-10", memo: "Roof",
      shares: [{ account: "a1", amount: "450.01" }, { account: "a2", amount: "450.00" }],
      markup: true,
    });
    expect(done()).toBe("Bill ROOF-1 is shared across 2 properties.");
  });

  it("keeps Record bill off until the shares come to the invoice, and adds or takes away a property", () => {
    const record = vi.fn(async () => true);
    render(<RecordBillForm books={TWO} record={record} />);
    fillShared("900.00");
    fireEvent.change(screen.getByLabelText("Amount, share 1"), { target: { value: "500.00" } });
    fireEvent.change(screen.getByLabelText("Amount, share 2"), { target: { value: "350.00" } });
    expect(screen.getByTestId("shares-total").textContent).toBe("Shares come to $850.00 of $900.00.");
    expect(screen.getByRole("button", { name: "Record bill" })).toHaveProperty("disabled", true);
    fireEvent.click(screen.getByRole("button", { name: "Add property" }));
    expect(screen.getByLabelText("Amount, share 3")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Remove share 3" }));
    expect(screen.queryByLabelText("Amount, share 3")).toBeNull();
    fireEvent.change(screen.getByLabelText("Amount, share 2"), { target: { value: "400.00" } });
    expect(screen.getByRole("button", { name: "Record bill" })).toHaveProperty("disabled", false);
    expect(record).not.toHaveBeenCalled();
  });

  it("offers the owner's markup only where their terms set one", async () => {
    const record = vi.fn(async () => true);
    render(<RecordBillForm books={TWO} record={record} />);
    fill();
    fireEvent.click(screen.getByLabelText("Add the owner's markup (10%)"));
    fireEvent.click(screen.getByRole("button", { name: "Record bill" }));
    await vi.waitFor(() => expect(record).toHaveBeenCalled());
    const [, body] = record.mock.calls[0] as unknown as [string, Record<string, unknown>];
    expect(body).toMatchObject({ account: "a1", amount: "75.00", markup: true });

    fireEvent.change(screen.getByLabelText("Owner and property"), { target: { value: "a2" } });
    expect(screen.queryByText(/markup/)).toBeNull();
  });

  it("sends no markup unless asked, and offers no sharing for a work order's bill", async () => {
    const record = vi.fn(async () => true);
    render(<RecordBillForm books={TWO} record={record}
      start={{ vendorId: "v1", propertyId: "pr1", workOrderId: "w1", memo: "Leak" }} />);
    expect(screen.queryByLabelText("Share across properties")).toBeNull();
    fill();
    fireEvent.click(screen.getByRole("button", { name: "Record bill" }));
    await vi.waitFor(() => expect(record).toHaveBeenCalled());
    const [path, body] = record.mock.calls[0] as unknown as [string, Record<string, unknown>];
    expect(path).toBe("/api/payables/bills");
    expect(body).not.toHaveProperty("markup");
    expect(body).toMatchObject({ work_order_id: "w1" });
  });
});

// Property 1 owned 60/40 by Owner 1 and Owner 3 (migration 0094); Property 2 by Owner 2 alone.
const CO_OWNED = {
  ...TWO,
  accounts: [
    { ...TWO.accounts[0]!, ownership_percent: "60" },
    { ...TWO.accounts[1]!, ownership_percent: null },
    { account: "a3", owner_id: "o3", owner: "Owner 3", property_id: "pr1", property: "Property 1", bank_account: "Operating",
      agreement: { ...TERMS, bill_markup_percent: "0" }, ownership_percent: "40" },
  ],
} as unknown as Portfolio;

describe("RecordBillForm split by ownership", () => {
  it("splits a co-owned property's bill between its owners by their shares unless unticked", async () => {
    const record = vi.fn(async () => true);
    render(<RecordBillForm books={CO_OWNED} record={record} />);
    fill();
    const split = screen.getByLabelText("Split by ownership: Owner 1 60% · Owner 3 40%") as HTMLInputElement;
    expect(split.checked).toBe(true);
    // One expense account for the whole bill; any owner's markup, as their terms set.
    expect(screen.queryByLabelText("Split across expense accounts")).toBeNull();
    fireEvent.change(screen.getByLabelText("Expense account"), { target: { value: "e1" } });
    fireEvent.click(screen.getByLabelText("Add each owner's markup, as their terms set"));
    fireEvent.click(screen.getByRole("button", { name: "Record bill" }));

    await vi.waitFor(() => expect(record).toHaveBeenCalled());
    const [path, body, done] = record.mock.calls[0] as unknown as [string, Record<string, unknown>, (a: { bills: string[] }) => string];
    expect(path).toBe("/api/payables/shared-bills");
    expect(body).toEqual({
      vendor_id: "v1", reference: "INV-9", bill_date: "2026-10-01", due_on: "2026-10-10", memo: "Hose",
      by_ownership: { account: "a1", amount: "75.00" }, expense_account_id: "e1", markup: true,
    });
    expect(done({ bills: ["b1", "b2"] })).toBe("Bill INV-9 is split between 2 owners by their shares.");

    // Unticked, it is one owner's bill as before.
    fireEvent.click(split);
    expect(screen.getByLabelText("Split across expense accounts")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Record bill" }));
    await vi.waitFor(() => expect(record).toHaveBeenCalledTimes(2));
    const [second, again] = record.mock.calls[1] as unknown as [string, Record<string, unknown>];
    expect(second).toBe("/api/payables/bills");
    expect(again).toMatchObject({ account: "a1", amount: "75.00" });
  });

  it("offers no split for a property without shares, a shared invoice or a work order's bill", () => {
    const record = vi.fn(async () => true);
    render(<RecordBillForm books={CO_OWNED} record={record} />);
    fireEvent.change(screen.getByLabelText("Owner and property"), { target: { value: "a2" } });
    expect(screen.queryByLabelText(/Split by ownership/)).toBeNull();
    fireEvent.click(screen.getByLabelText("Share across properties"));
    expect(screen.queryByLabelText(/Split by ownership/)).toBeNull();
    cleanup();
    render(<RecordBillForm books={CO_OWNED} record={record}
      start={{ vendorId: "v1", propertyId: "pr1", workOrderId: "w1", memo: "Leak" }} />);
    expect(screen.queryByLabelText(/Split by ownership/)).toBeNull();
  });
});

describe("linesBody", () => {
  it("sends one account, the lines, or nothing", () => {
    expect(linesBody(false, "e1", [])).toEqual({ expense_account_id: "e1" });
    expect(linesBody(false, "", [])).toEqual({});
    expect(linesBody(true, "", [{ account: "e1", amount: "1.00" }])).toEqual({ lines: [{ expense_account_id: "e1", amount: "1.00" }] });
  });
});
