import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ErrorBoundary } from "./ErrorBoundary";

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

function Breaks(): React.ReactNode {
  throw new Error("an answer of a shape the page doesn't know");
}

describe("ErrorBoundary", () => {
  it("shows what to do instead of a blank page when a page fails to draw (H-152)", () => {
    vi.spyOn(console, "error").mockImplementation(() => undefined); // React reports the error
    const reload = vi.fn();
    render(<ErrorBoundary reload={reload}><Breaks /></ErrorBoundary>);
    const alert = screen.getByRole("alert");
    expect(alert.textContent).toContain("This page couldn't be shown.");
    expect(alert.textContent).not.toContain("shape the page doesn't know"); // no internals
    fireEvent.click(screen.getByRole("button", { name: "Reload" }));
    expect(reload).toHaveBeenCalledTimes(1);
    expect(screen.getByRole("link", { name: "Go to the start" }).getAttribute("href")).toBe("/");
  });

  it("shows the page when nothing fails", () => {
    render(<ErrorBoundary><p>Rent roll</p></ErrorBoundary>);
    expect(screen.getByText("Rent roll")).toBeTruthy();
    expect(screen.queryByRole("alert")).toBeNull();
  });
});
