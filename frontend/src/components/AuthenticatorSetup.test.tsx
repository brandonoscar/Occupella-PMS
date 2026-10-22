import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import { AuthenticatorSetup, qrDataUrl, secretOf } from "./AuthenticatorSetup";

afterEach(cleanup);

const URI = "otpauth://totp/Occupella%20PMS:s%40example.com?secret=JBSWY3DPEHPK3PXP&issuer=Occupella%20PMS";

describe("authenticator setup", () => {
  it("shows the key from the setup link, to type into the app", () => {
    expect(secretOf(URI)).toBe("JBSWY3DPEHPK3PXP");
  });

  it("draws the setup link as a QR code image", () => {
    expect(qrDataUrl(URI)).toMatch(/^data:image\/gif;base64,/);
  });

  it("shows the QR code and the key under it", () => {
    const { container } = render(<AuthenticatorSetup uri={URI} />);
    expect(screen.getByRole("img", { name: "Authenticator setup code" }).getAttribute("src")).toMatch(/^data:image\/gif/);
    expect(container.querySelector("code")?.textContent).toBe("JBSWY3DPEHPK3PXP");
  });
});
