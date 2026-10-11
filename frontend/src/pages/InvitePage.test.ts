import { describe, expect, it } from "vitest";
import { qrDataUrl, secretOf } from "./InvitePage";

const URI = "otpauth://totp/Occupella%20PMS:s%40example.com?secret=JBSWY3DPEHPK3PXP&issuer=Occupella%20PMS";

describe("authenticator setup", () => {
  it("shows the key from the setup link, to type into the app", () => {
    expect(secretOf(URI)).toBe("JBSWY3DPEHPK3PXP");
  });

  it("draws the setup link as a QR code image", () => {
    expect(qrDataUrl(URI)).toMatch(/^data:image\/gif;base64,/);
  });
});
