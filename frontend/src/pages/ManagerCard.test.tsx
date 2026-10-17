import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ManagerCard } from "./ManagerCard";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

const answer = (body: unknown, status = 200) =>
  vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify(body), { status })));

describe("ManagerCard", () => {
  it("shows the manager's logo, name and mailing address", async () => {
    answer({
      name: "Synthetic PMC", logo: true,
      address: { address_line_1: "100 Example Street", address_line_2: null, city: "Sampletown", state: "ZZ", postal_code: "00000" },
    });
    render(<ManagerCard />);
    expect((await screen.findByText("Synthetic PMC")).closest("address")?.textContent).toBe(
      "Synthetic PMC100 Example StreetSampletown, ZZ 00000",
    );
    expect(screen.getByRole("img", { name: "Synthetic PMC logo" }).getAttribute("src")).toBe("/api/company/logo");
  });

  it("shows the name alone when there is no address or logo, and why it can't be read", async () => {
    answer({ name: "Synthetic PMC", logo: false, address: null });
    render(<ManagerCard />);
    expect((await screen.findByText("Synthetic PMC")).closest("address")?.textContent).toBe("Synthetic PMC");
    expect(screen.queryByRole("img")).toBeNull();
    cleanup();

    answer({ detail: "sign in first" }, 401);
    render(<ManagerCard />);
    expect((await screen.findByRole("alert")).textContent).toBe("sign in first");
  });
});
