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

describe("linesBody", () => {
  it("sends one account, the lines, or nothing", () => {
    expect(linesBody(false, "e1", [])).toEqual({ expense_account_id: "e1" });
    expect(linesBody(false, "", [])).toEqual({});
    expect(linesBody(true, "", [{ account: "e1", amount: "1.00" }])).toEqual({ lines: [{ expense_account_id: "e1", amount: "1.00" }] });
  });
});
