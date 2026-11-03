import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import { LegalLinks } from "./LegalLinks";

afterEach(cleanup);

describe("LegalLinks", () => {
  it("names the company's terms and privacy notice, each opening on its own site", () => {
    render(<LegalLinks company="Synthetic Homes" legal={{ terms_url: "https://example.com/terms", privacy_url: "https://example.com/privacy" }} />);

    expect(screen.getByText(/Synthetic Homes's/).textContent).toBe("Synthetic Homes's terms of use and privacy notice");
    for (const [name, href] of [["terms of use", "https://example.com/terms"], ["privacy notice", "https://example.com/privacy"]]) {
      const link = screen.getByRole("link", { name });
      expect(link.getAttribute("href")).toBe(href);
      expect(link.getAttribute("target")).toBe("_blank");
      expect(link.getAttribute("rel")).toBe("noopener noreferrer");
    }
  });

  it("names only what the company set", () => {
    render(<LegalLinks company="Synthetic Homes" legal={{ terms_url: null, privacy_url: "https://example.com/privacy" }} />);

    expect(screen.getByText(/Synthetic Homes's/).textContent).toBe("Synthetic Homes's privacy notice");
    expect(screen.queryByRole("link", { name: "terms of use" })).toBeNull();
  });

  it("shows nothing when the company set neither", () => {
    const { container } = render(<LegalLinks company="Synthetic Homes" legal={{ terms_url: null, privacy_url: null }} />);

    expect(container.textContent).toBe("");
  });
});
