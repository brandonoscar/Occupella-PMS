import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { useState } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ConfirmDialog } from "./Confirm";
import { SidePanel } from "./SidePanel";

afterEach(cleanup);

// A page with a button that opens a panel holding a form, as the pages do.
function Page({ onClose = () => {} }: { onClose?: () => void }) {
  const [open, setOpen] = useState(false);
  return (
    <>
      <button type="button" onClick={() => setOpen(true)}>Record bill</button>
      <a href="/elsewhere">A link behind the panel</a>
      {open && (
        <SidePanel title="Record bill" onClose={() => { onClose(); setOpen(false); }}>
          <label>Amount <input /></label>
          <button type="submit">Save</button>
        </SidePanel>
      )}
    </>
  );
}

describe("SidePanel", () => {
  it("is a dialog named by its title, takes focus and keeps it inside while open", async () => {
    render(<Page />);
    const opener = screen.getByRole("button", { name: "Record bill" });
    opener.focus();
    fireEvent.click(opener);

    const panel = screen.getByRole("dialog", { name: "Record bill" });
    await vi.waitFor(() => expect(panel.contains(document.activeElement)).toBe(true));
    expect(within(panel).getByRole("heading", { name: "Record bill" })).toBeTruthy();
    // The page behind is hidden from screen readers while the panel is open.
    expect(screen.queryByRole("link", { name: "A link behind the panel" })).toBeNull();

    // Tab from the last control comes back to the first, never to the page behind.
    const save = within(panel).getByRole("button", { name: "Save" });
    save.focus();
    fireEvent.keyDown(save, { key: "Tab" });
    expect(panel.contains(document.activeElement)).toBe(true);
    expect(document.activeElement).not.toBe(save);
  });

  it("closes on Escape and on Close, giving focus back to what opened it", async () => {
    const onClose = vi.fn();
    render(<Page onClose={onClose} />);
    const opener = screen.getByRole("button", { name: "Record bill" });
    opener.focus();
    fireEvent.click(opener);
    fireEvent.keyDown(within(screen.getByRole("dialog")).getByRole("textbox"), { key: "Escape" });
    expect(screen.queryByRole("dialog")).toBeNull();
    await vi.waitFor(() => expect(document.activeElement).toBe(opener));

    fireEvent.click(opener);
    fireEvent.click(within(screen.getByRole("dialog")).getByRole("button", { name: "Close" }));
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(onClose).toHaveBeenCalledTimes(2);
  });
});

describe("ConfirmDialog", () => {
  it("asks as an alert dialog with the confirm button focused, and Escape cancels", async () => {
    const onConfirm = vi.fn();
    const onCancel = vi.fn();
    render(<ConfirmDialog question="Pay $1,200.00 to Demo Owner A?" confirmLabel="Pay" onConfirm={onConfirm} onCancel={onCancel} />);

    const asked = screen.getByRole("alertdialog", { name: "Pay $1,200.00 to Demo Owner A?" });
    await vi.waitFor(() => expect(document.activeElement).toBe(within(asked).getByRole("button", { name: "Pay" })));
    fireEvent.keyDown(document.activeElement!, { key: "Escape" });
    expect(onCancel).toHaveBeenCalledTimes(1);
    expect(onConfirm).not.toHaveBeenCalled();
  });
});
