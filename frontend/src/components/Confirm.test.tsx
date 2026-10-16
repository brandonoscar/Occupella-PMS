import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { useConfirm } from "./Confirm";

afterEach(cleanup);

function Asker({ act }: { act: () => void }) {
  const { ask, prompt } = useConfirm();
  return (
    <>
      <button type="button" onClick={() => ask("Pay $80.00 to Vendor 1?", act)}>Pay</button>
      {prompt}
    </>
  );
}

describe("useConfirm", () => {
  it("asks first, and acts once on Confirm", () => {
    const act = vi.fn();
    render(<Asker act={act} />);
    expect(screen.queryByRole("alertdialog")).toBeNull();

    fireEvent.click(screen.getByRole("button", { name: "Pay" }));
    expect(screen.getByRole("alertdialog", { name: "Pay $80.00 to Vendor 1?" }).textContent).toContain(
      "Pay $80.00 to Vendor 1?",
    );
    expect(act).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "Confirm" }));
    expect(act).toHaveBeenCalledTimes(1);
    expect(screen.queryByRole("alertdialog")).toBeNull();
  });

  it("does nothing on Cancel", () => {
    const act = vi.fn();
    render(<Asker act={act} />);
    fireEvent.click(screen.getByRole("button", { name: "Pay" }));
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
    expect(screen.queryByRole("alertdialog")).toBeNull();
    expect(act).not.toHaveBeenCalled();
  });
});
