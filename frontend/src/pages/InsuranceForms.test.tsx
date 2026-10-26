import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { RentLease } from "../rent";
import { InsuranceForms } from "./InsuranceForms";

afterEach(cleanup);

// Only what the forms read of a lease.
const LEASE = { id: "l1", renters_insurance: null } as unknown as RentLease;

// The plain line under a button (ADR 0010, rule 1): what describes it.
const says = (name: string) => {
  const button = screen.getByRole("button", { name });
  return document.getElementById(button.getAttribute("aria-describedby") ?? "")?.textContent;
};

describe("InsuranceForms", () => {
  it("says what setting the terms does, by whether the lease will ask for renters insurance", () => {
    render(<InsuranceForms lease={LEASE} record={vi.fn(async () => true)} />);
    expect(says("Set insurance terms"))
      .toBe("From that day the lease doesn't ask for renters insurance; earlier terms stay. Nothing is charged.");
    fireEvent.click(screen.getByLabelText("Requires renters insurance"));
    expect(says("Set insurance terms")).toBe(
      "The lease asks for renters insurance from that day; the tenant sees it in their portal. Nothing is charged.",
    );
    expect(says("Record policy"))
      .toBe("Adds this policy to the lease; earlier ones stay on file. Nothing is charged, and the insurer isn't contacted.");
  });
});
