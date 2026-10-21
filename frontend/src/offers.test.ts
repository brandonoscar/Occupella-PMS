import { describe, expect, it } from "vitest";
import type { Offer } from "./offers";
import { base64Of, dayAfter, isPdf, offerBadge, parseSigners, sentLine, signedCount, signersText, term } from "./offers";

const OFFER: Offer = {
  id: "o1",
  kind: "new lease",
  application_id: "a1",
  renews_lease_id: null,
  property_id: "p1",
  property: "Property 1",
  unit_id: "u1",
  unit: "Unit 1",
  starts_on: "2027-01-01",
  ends_on: null,
  monthly_rent: "1500.00",
  document_title: "Lease",
  offered_at: "2026-10-11T18:00:00Z",
  sent: true,
  signers: [
    { position: 1, name: "A", email: "a@example.com", role: "tenant", signed_at: "2026-10-11T19:00:00Z", progress: "signed" },
    { position: 2, name: "B", email: "b@example.com", role: "tenant", signed_at: null, progress: "waiting" },
  ],
  lease_id: null,
  security_deposit: null,
  recurring_charges: [],
  status: "partially signed",
  void: null,
};

describe("sentLine", () => {
  it("says whom Send went to and whom the PMS emailed their link", () => {
    expect(sentLine({ offer: OFFER, links: [] })).toBe("Sent to 2 signers.");
    expect(sentLine({ offer: OFFER, links: [], emailed: [] })).toBe("Sent to 2 signers.");
    expect(sentLine({ offer: OFFER, links: [], emailed: ["a@example.com", "b@example.com"] })).toBe(
      "Sent to 2 signers. Each was emailed their link: a@example.com, b@example.com.",
    );
    expect(sentLine({ offer: { ...OFFER, signers: OFFER.signers.slice(0, 1) }, links: [] })).toBe("Sent to 1 signer.");
  });
});

describe("offerBadge and signedCount", () => {
  it("counts who has signed", () => {
    expect(signedCount(OFFER)).toBe("1 of 2 signed");
  });
  it("reads each status in a badge", () => {
    const all = ["draft", "sent", "partially signed", "signed", "leased", "void"] as const;
    expect(all.map((status) => offerBadge(status))).toEqual([
      { word: "Draft", tone: "neutral" },
      { word: "Sent", tone: "info" },
      { word: "Partially signed", tone: "caution" },
      { word: "Signed", tone: "positive" },
      { word: "Lease active", tone: "positive" },
      { word: "Void", tone: "danger" },
    ]);
  });
});

describe("parseSigners", () => {
  it("reads one signer a line, the email after the last comma", () => {
    expect(parseSigners("Doe, Applicant, a@example.com\n\n  B Person ,b@example.com  ")).toEqual({
      signers: [
        { name: "Doe, Applicant", email: "a@example.com" },
        { name: "B Person", email: "b@example.com" },
      ],
    });
  });
  it("names the line that has no name or no email", () => {
    expect(parseSigners("A, a@example.com\nB")).toEqual({
      error: 'line 2: write each signer as "Name, email"',
    });
    expect(parseSigners(", a@example.com")).toEqual({
      error: 'line 1: write each signer as "Name, email"',
    });
    expect(parseSigners("A,  ")).toEqual({ error: 'line 1: write each signer as "Name, email"' });
  });
  it("needs someone", () => {
    expect(parseSigners(" \n")).toEqual({ error: "name who signs" });
  });
  it("reads back what signersText writes", () => {
    const people = [{ name: "A", email: "a@example.com" }];
    expect(parseSigners(signersText(people))).toEqual({ signers: people });
  });
});

describe("signersText", () => {
  it("leaves the email to fill in for someone without one", () => {
    expect(signersText([{ name: "A", email: "a@example.com" }, { name: "B", email: null }])).toBe(
      "A, a@example.com\nB, ",
    );
  });
});

describe("term and dayAfter", () => {
  it("reads a lease's term", () => {
    expect(term("2027-01-01", "2027-06-30")).toBe("01/01/2027 – 06/30/2027");
    expect(term("2027-01-01", null)).toBe("01/01/2027 – Month to month");
  });
  it("finds the next day across a month and a year", () => {
    expect(dayAfter("2027-06-30")).toBe("2027-07-01");
    expect(dayAfter("2026-12-31")).toBe("2027-01-01");
    expect(dayAfter("2028-02-28")).toBe("2028-02-29");
  });
});

describe("documents", () => {
  const bytes = (text: string) => new TextEncoder().encode(text);
  it("knows a PDF by its first bytes", () => {
    expect(isPdf(bytes("%PDF-1.7 rest"))).toBe(true);
    expect(isPdf(bytes("%PDF"))).toBe(false);
    expect(isPdf(bytes("hello world"))).toBe(false);
  });
  it("encodes base64 the way the server decodes it, past one chunk", () => {
    expect(base64Of(bytes("%PDF-1.4 a synthetic lease"))).toBe(btoa("%PDF-1.4 a synthetic lease"));
    const large = new Uint8Array(0x8000 * 2 + 3).map((_, n) => n % 251);
    expect(atob(base64Of(large))).toBe(Array.from(large, (c) => String.fromCharCode(c)).join(""));
  });
});
