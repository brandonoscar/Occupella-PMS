import { describe, expect, it } from "vitest";
import type { Signer } from "./signers";
import { signerLine, standingTone } from "./signers";

const BROKER: Signer = {
  staff_id: "s1", name: "Broker 1", role: "broker", license_number: "LIC-0001", bond_amount: null,
  bond_expires_on: null, authorized_on: "2026-01-01", standing: "licensed",
};

describe("signers", () => {
  it("reads the broker by license and a bonded signer by bond", () => {
    expect(signerLine(BROKER)).toBe("Broker 1, the broker: license LIC-0001, authorized 01/01/2026");
    expect(signerLine({
      ...BROKER, name: "Clerk 1", role: "signer", license_number: null, bond_amount: "25000.00",
      bond_expires_on: "2026-12-31", standing: "bonded",
    })).toBe("Clerk 1: bonded for $25,000.00 through 12/31/2026, authorized 01/01/2026");
  });

  it("marks a bond running out as a caution and one run out as danger", () => {
    expect(standingTone("licensed")).toBe("neutral");
    expect(standingTone("bonded")).toBe("neutral");
    expect(standingTone("bond runs out in 3 days")).toBe("caution");
    expect(standingTone("bond ran out")).toBe("danger");
  });
});
