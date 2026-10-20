import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { RentLease } from "../rent";
import { ChargeTypesCard } from "./ChargeTypes";
import { ChargeForm, RecurringForm } from "./LeaseForms";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

function server(types: { id: string; name: string }[]) {
  let now = types;
  const fetch = vi.fn(async (_path: string, init?: RequestInit) => {
    if (init?.method === "POST") {
      const name = JSON.parse(String(init.body)).name;
      now = [...now, { id: `t${now.length + 1}`, name }];
      return new Response(JSON.stringify({ id: `t${now.length}` }), { status: 201 });
    }
    return new Response(JSON.stringify(now), { status: 200 });
  });
  vi.stubGlobal("fetch", fetch);
  return fetch;
}

const LEASE = { id: "l1" } as RentLease;

describe("ChargeTypesCard", () => {
  it("lists the company's charge types and adds one in a side panel", async () => {
    const fetch = server([]);
    render(<ChargeTypesCard say={() => {}} />);
    expect(await screen.findByText("No charge types yet")).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: "Add charge type" }));
    const panel = screen.getByRole("dialog", { name: "Add charge type" });
    fireEvent.change(within(panel).getByLabelText(/Charge type/), { target: { value: "Application fee" } });
    fireEvent.click(within(panel).getByRole("button", { name: "Add charge type" }));

    const list = await screen.findByRole("list", { name: "Charge types" });
    expect(within(list).getByText("Application fee")).toBeTruthy();
    const post = fetch.mock.calls.find(([, init]) => init?.method === "POST");
    expect(post?.[0]).toBe("/api/setup/charge-types");
    expect(JSON.parse(String(post?.[1]?.body))).toEqual({ name: "Application fee" });
  });
});

describe("Enter charge and monthly charges", () => {
  it("give a fee one of the company's types, and never a credit", async () => {
    server([{ id: "t1", name: "Key replacement" }]);
    const record = vi.fn(async () => true);
    render(<ChargeForm lease={LEASE} record={record} />);
    fireEvent.change(screen.getByLabelText(/Amount/), { target: { value: "25.00" } });
    fireEvent.change(screen.getByLabelText(/What for/), { target: { value: "Lost key" } });
    fireEvent.change(await screen.findByLabelText("Type"), { target: { value: "t1" } });
    fireEvent.click(screen.getByRole("button", { name: "Add" }));
    await vi.waitFor(() => expect(record).toHaveBeenCalledTimes(1));
    expect(record.mock.calls[0]).toEqual(expect.arrayContaining([
      "/api/rent/leases/l1/charges",
      expect.objectContaining({ kind: "fee", amount: "25.00", charge_type_id: "t1" }),
    ]));

    // A credit has no type to pick, and none is sent.
    const add = screen.getByRole("button", { name: "Add" }) as HTMLButtonElement;
    await vi.waitFor(() => expect(add.disabled).toBe(false));
    fireEvent.change(screen.getByLabelText("Add a"), { target: { value: "credit" } });
    expect(screen.queryByLabelText("Type")).toBeNull();
    fireEvent.change(screen.getByLabelText(/Amount/), { target: { value: "5.00" } });
    fireEvent.change(screen.getByLabelText(/What for/), { target: { value: "Goodwill" } });
    fireEvent.click(screen.getByRole("button", { name: "Add" }));
    await vi.waitFor(() => expect(record).toHaveBeenCalledTimes(2));
    expect((record.mock.calls[1] as unknown[])[1]).not.toHaveProperty("charge_type_id");
  });

  it("show no type until the company has one", async () => {
    const fetch = server([]);
    const record = vi.fn(async () => true);
    render(<RecurringForm lease={LEASE} record={record} />);
    await vi.waitFor(() => expect(fetch).toHaveBeenCalledWith("/api/setup/charge-types", expect.anything()));
    expect(screen.queryByLabelText("Type")).toBeNull();
  });
});
