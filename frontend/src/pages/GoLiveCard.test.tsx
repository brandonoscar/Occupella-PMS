import { cleanup, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import type { GoLive } from "./GoLiveCard";
import { GoLiveCard } from "./GoLiveCard";

afterEach(cleanup);

const STEPS: GoLive["steps"] = [
  { key: "company", label: "Company details", detail: "Your mailing address", done: true, required: true, href: "/settings/setup" },
  { key: "leases", label: "Leases in place", detail: "Leases and their tenants", done: false, required: true, href: "/rent-roll" },
  { key: "team", label: "A second person for month-end", detail: "Another approves it", done: false, required: true, href: "/settings/staff" },
  { key: "online_rent", label: "Rent paid online", detail: "Tenants pay by bank", done: false, required: false, href: "/settings/setup" },
];

describe("GoLiveCard", () => {
  it("lists every step, says how many required ones are left, and links each to its page", () => {
    render(<GoLiveCard goLive={{ steps: STEPS, ready: false }} />);

    expect(screen.getByText("2 steps left before you go live.")).toBeTruthy();
    const items = within(screen.getByRole("list", { name: "Getting started" })).getAllByRole("listitem");
    expect(items).toHaveLength(4);
    expect(within(items[0]!).getByText("Done")).toBeTruthy();
    expect(within(items[1]!).getByText("To do")).toBeTruthy();
    expect(screen.getByRole("link", { name: "Leases in place" }).getAttribute("href")).toBe("/rent-roll");
    expect(screen.getByText("Tenants pay by bank (optional)")).toBeTruthy();
  });

  it("says one step in the singular", () => {
    const steps = STEPS.map((step) => (step.key === "team" ? { ...step, done: true } : step));
    render(<GoLiveCard goLive={{ steps, ready: false }} />);
    expect(screen.getByText("1 step left before you go live.")).toBeTruthy();
  });

  it("shows nothing once the company is ready, whatever optional step is left", () => {
    const { container } = render(<GoLiveCard goLive={{ steps: STEPS, ready: true }} />);
    expect(container.textContent).toBe("");
  });
});
