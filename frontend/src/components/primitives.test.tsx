import { cleanup, render, screen } from "@testing-library/react";
import { House } from "lucide-react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { Badge } from "./Badge";
import { Button } from "./Button";
import { EmptyState } from "./EmptyState";
import { Notice } from "./Notice";
import { Wide } from "./Wide";

afterEach(cleanup);

describe("Badge", () => {
  it("is a small pill in the colour of its state, its word kept as it came", () => {
    render(
      <>
        <Badge tone="danger" data-testid="bounced">bounced</Badge>
        <Badge>none</Badge>
      </>,
    );
    const bounced = screen.getByTestId("bounced");
    expect(bounced.textContent).toBe("bounced");
    expect(bounced.getAttribute("data-tone")).toBe("danger");
    expect(bounced.className).toContain("rounded-full");
    expect(bounced.className).toContain("text-danger");
    // Only the first letter is shown as a capital.
    expect(bounced.querySelector("span")?.className).toContain("first-letter:uppercase");
    expect(screen.getByText("none").closest("[data-tone]")?.getAttribute("data-tone")).toBe("neutral");
  });
});

describe("Notice", () => {
  it("announces an error with an icon beside it, and keeps the API's words", () => {
    render(<Notice tone="error">the email or password is wrong</Notice>);
    const alert = screen.getByRole("alert");
    expect(alert.textContent).toBe("the email or password is wrong");
    expect(alert.querySelector("svg")?.getAttribute("aria-hidden")).toBe("true");
    expect(alert.className).toContain("text-danger");
    expect(alert.querySelector("span")?.className).toContain("first-letter:uppercase");
  });

  it("says anything else quietly, and nothing at all when there is nothing to say", () => {
    const { container } = render(
      <>
        <Notice tone="info">saved</Notice>
        <Notice tone="error">{""}</Notice>
      </>,
    );
    expect(screen.getByRole("status").textContent).toBe("saved");
    expect(screen.getByRole("status").className).toContain("text-ink-muted");
    expect(screen.queryByRole("alert")).toBeNull();
    expect(container.querySelectorAll("svg")).toHaveLength(0);
  });
});

describe("Button", () => {
  it("puts its plain line under it and is described by it (rule 1)", () => {
    render(<Button type="submit" says="Records the payment. Nothing is sent to the tenant.">Record</Button>);
    const button = screen.getByRole("button", { name: "Record" });
    const says = screen.getByTestId("button-says");
    expect(says.textContent).toBe("Records the payment. Nothing is sent to the tenant.");
    expect(button.getAttribute("aria-describedby")).toBe(says.id);
    expect(says.className).toContain("text-ink-muted");
  });

  it("has no line and no description without one", () => {
    render(<Button>Cancel</Button>);
    expect(screen.getByRole("button", { name: "Cancel" }).getAttribute("aria-describedby")).toBeNull();
    expect(screen.queryByTestId("button-says")).toBeNull();
  });
  it("is 36px by default and 32px as a row's action", () => {
    render(
      <>
        <Button>Record payment</Button>
        <Button size="sm" variant="ghost">It bounced</Button>
      </>,
    );
    expect(screen.getByRole("button", { name: "Record payment" }).className).toContain("h-9");
    const small = screen.getByRole("button", { name: "It bounced" });
    expect(small.className).toContain("h-8");
    expect(small.className).not.toContain("h-9");
  });
});

describe("EmptyState", () => {
  it("is an icon, one calm sentence and what comes next", () => {
    render(<EmptyState icon={House} title="Nothing is available right now." body="Check back soon." />);
    const empty = screen.getByTestId("empty-state");
    expect(empty.querySelector("svg")?.getAttribute("aria-hidden")).toBe("true");
    expect(empty.textContent).toBe("Nothing is available right now.Check back soon.");
  });
});

describe("Wide", () => {
  it("lets a wide table scroll sideways inside its frame, not the page", () => {
    render(<Wide><table aria-label="Ledger"><tbody><tr><td>1</td></tr></tbody></table></Wide>);
    const frame = screen.getByRole("table", { name: "Ledger" }).parentElement;
    expect(frame?.className).toBe("overflow-x-auto");
    // It fits: no stop in the tab order.
    expect(frame?.hasAttribute("tabindex")).toBe(false);
  });

  it("takes keyboard focus while it scrolls, so the arrow keys scroll it", () => {
    const wider = vi.spyOn(HTMLElement.prototype, "scrollWidth", "get").mockReturnValue(800);
    const shown = vi.spyOn(HTMLElement.prototype, "clientWidth", "get").mockReturnValue(390);
    try {
      render(<Wide><table aria-label="Ledger"><tbody><tr><td>1</td></tr></tbody></table></Wide>);
      const frame = screen.getByRole("region", { name: "Ledger, scrolls sideways" });
      expect(frame.getAttribute("tabindex")).toBe("0");
      expect(frame.contains(screen.getByRole("table", { name: "Ledger" }))).toBe(true);
    } finally {
      wider.mockRestore();
      shown.mockRestore();
    }
  });
});
