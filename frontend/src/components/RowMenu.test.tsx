import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { RowMenu } from "./RowMenu";

afterEach(cleanup);

describe("RowMenu", () => {
  it("keeps a row's risky actions behind ⋯ and confirms one, naming its amount", () => {
    const bounce = vi.fn();
    const archive = vi.fn();
    render(
      <RowMenu label="09/01/2026 payment of $1,500.00" actions={[
        { label: "It bounced", danger: true, confirm: "Mark the $1,500.00 payment bounced?", onSelect: bounce },
        { label: "Archive", onSelect: archive },
      ]} />,
    );
    const open = screen.getByRole("button", { name: "Actions for 09/01/2026 payment of $1,500.00" });
    expect(open.getAttribute("aria-expanded")).toBe("false");
    expect(screen.queryByRole("menu")).toBeNull();

    fireEvent.click(open);
    expect(open.getAttribute("aria-expanded")).toBe("true");
    expect(within(screen.getByRole("menu")).getAllByRole("menuitem").map((i) => i.textContent)).toEqual([
      "It bounced", "Archive",
    ]);
    fireEvent.click(screen.getByRole("menuitem", { name: "It bounced" }));
    expect(screen.queryByRole("menu")).toBeNull();
    const dialog = screen.getByRole("alertdialog", { name: "Mark the $1,500.00 payment bounced?" });
    fireEvent.click(within(dialog).getByRole("button", { name: "Cancel" }));
    expect(bounce).not.toHaveBeenCalled();

    fireEvent.click(open);
    fireEvent.click(screen.getByRole("menuitem", { name: "It bounced" }));
    fireEvent.click(within(screen.getByRole("alertdialog")).getByRole("button", { name: "It bounced" }));
    expect(bounce).toHaveBeenCalledTimes(1);
    expect(screen.queryByRole("alertdialog")).toBeNull();

    // An action without a question acts at once.
    fireEvent.click(open);
    fireEvent.click(screen.getByRole("menuitem", { name: "Archive" }));
    expect(archive).toHaveBeenCalledTimes(1);
  });

  // A click outside it closes it too: React Aria's own pointer handling, which a real browser
  // gives and jsdom doesn't, so features.spec.ts (staff waive a late fee) checks that one.
  it("closes on Escape, and a dialog on Escape", () => {
    render(<RowMenu label="Unit 3" actions={[{ label: "Archive", confirm: "Archive Unit 3?", onSelect: vi.fn() }]} />);
    const open = screen.getByRole("button", { name: "Actions for Unit 3" });
    fireEvent.click(open);
    fireEvent.keyDown(screen.getByRole("menu"), { key: "Escape" });
    expect(screen.queryByRole("menu")).toBeNull();
    fireEvent.click(open);
    fireEvent.click(screen.getByRole("menuitem", { name: "Archive" }));
    fireEvent.keyDown(document.activeElement!, { key: "Escape" });
    expect(screen.queryByRole("alertdialog")).toBeNull();
  });

  it("opens from the keyboard on its first action, moves with the arrow keys and gives focus back", async () => {
    const archive = vi.fn();
    render(
      <RowMenu label="Unit 3" actions={[
        { label: "Rename", onSelect: vi.fn() },
        { label: "Archive", onSelect: archive },
      ]} />,
    );
    const open = screen.getByRole("button", { name: "Actions for Unit 3" });
    open.focus();
    fireEvent.keyDown(open, { key: "ArrowDown" });
    await vi.waitFor(() => expect(document.activeElement?.textContent).toBe("Rename"));
    fireEvent.keyDown(document.activeElement!, { key: "ArrowDown" });
    expect(document.activeElement?.textContent).toBe("Archive");
    fireEvent.keyDown(document.activeElement!, { key: "Escape" });
    expect(screen.queryByRole("menu")).toBeNull();
    await vi.waitFor(() => expect(document.activeElement).toBe(open));

    fireEvent.keyDown(open, { key: "ArrowDown" });
    await vi.waitFor(() => expect(document.activeElement?.textContent).toBe("Rename"));
    fireEvent.keyDown(document.activeElement!, { key: "ArrowDown" });
    fireEvent.keyDown(document.activeElement!, { key: "Enter" });
    expect(archive).toHaveBeenCalledTimes(1);
  });
});
