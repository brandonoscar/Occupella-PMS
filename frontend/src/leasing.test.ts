import { describe, expect, it } from "vitest";
import type { Application, OpenUnit } from "./leasing";
import {
  againstScreening,
  applicantNames,
  applicantStatus,
  applicationBadge,
  applyVia,
  incomeMultiple,
  latestScreening,
  feedPhone,
  feedStanding,
  needsNotice,
  recommendationLabel,
} from "./leasing";

describe("leasing helpers", () => {
  it("says when a decision goes against the latest screening", () => {
    const screened = (...recommendations: string[]) => ({ screenings: recommendations.map((recommendation) => ({ recommendation })) });
    expect(againstScreening(screened(), "declined")).toBe(false);
    expect(againstScreening(screened("approve"), "approved")).toBe(false);
    expect(againstScreening(screened("approve"), "declined")).toBe(true);
    expect(againstScreening(screened("conditional"), "conditional")).toBe(false);
    expect(againstScreening(screened("conditional"), "approved")).toBe(true);
    expect(againstScreening(screened("decline"), "declined")).toBe(false);
    // Only the latest counts.
    expect(againstScreening(screened("decline", "approve"), "approved")).toBe(false);
  });

  it("tells an applicant where their application stands", () => {
    expect(applicantStatus("received")).toContain("hasn't looked at it yet");
    expect(applicantStatus("in review")).toBe("In review.");
    expect(applicantStatus("approved")).toBe("Approved.");
    expect(applicantStatus("conditional")).toBe("Approved, on conditions:");
    expect(applicantStatus("declined")).toContain("Declined");
    expect(applicantStatus("withdrawn")).toBe("Withdrawn.");
  });

  it("reads an application's status for staff in a badge", () => {
    const all = ["new", "screening", "approved", "conditional", "rejected", "withdrawn"] as const;
    expect(all.map((status) => applicationBadge(status))).toEqual([
      { word: "New", tone: "info" },
      { word: "Screening", tone: "caution" },
      { word: "Approved", tone: "positive" },
      { word: "Approved on conditions", tone: "positive" },
      { word: "Rejected", tone: "danger" },
      { word: "Withdrawn", tone: "neutral" },
    ]);
  });

  it("names the applicants and what the latest screening recommends", () => {
    const application = {
      applicants: [{ name: "Applicant A", email: "a@example.com" }, { name: "Applicant B", email: "b@example.com" }],
      screenings: [],
    } as unknown as Application;
    const screened = {
      ...application,
      screenings: [
        { id: "s1", partner: "Fake", reference: "r1", recommendation: "decline", reasons: ["x"], reported_at: "2026-10-01T00:00:00Z" },
        { id: "s2", partner: "Fake", reference: "r2", recommendation: "approve", reasons: [], reported_at: "2026-10-02T00:00:00Z" },
      ],
    } as Application;
    expect(applicantNames(application)).toBe("Applicant A, Applicant B");
    expect(latestScreening(application)).toBe("");
    expect(latestScreening(screened)).toBe("Recommends approval");
  });

  it("names what a screening recommends", () => {
    expect(["approve", "conditional", "decline"].map((r) => recommendationLabel(r as "approve"))).toEqual([
      "Recommends approval",
      "Recommends conditions",
      "Recommends declining",
    ]);
  });

  it("asks for a notice exactly when a decision against the applicant rests on a screening", () => {
    expect(needsNotice("declined", "s1")).toBe(true);
    expect(needsNotice("conditional", "s1")).toBe(true);
    expect(needsNotice("approved", "s1")).toBe(false);
    expect(needsNotice("declined", null)).toBe(false);
  });

  it("shows income against rent, rounded down, and nothing without a rent", () => {
    expect(incomeMultiple("4500.00", "1500.00")).toBe("3.0x");
    expect(incomeMultiple("4499.99", "1500.00")).toBe("2.9x");
    expect(incomeMultiple("4500.00", "0")).toBe("");
  });
});

describe("applyVia", () => {
  it("applies through a listing when the page names one, else the link's token", () => {
    expect(applyVia("?listing=v1", "")).toEqual({ listing: "v1" });
    expect(applyVia("?listing=v1", "#token")).toEqual({ listing: "v1" });
    expect(applyVia("", "#token-1")).toEqual({ token: "token-1" });
    expect(applyVia("?other=x", "#token-1")).toEqual({ token: "token-1" });
  });
});

describe("listing feeds", () => {
  const unit = (extra: Partial<OpenUnit>): OpenUnit => ({
    unit_id: "u1", property_id: "p1", property: "Property 1", unit: "Unit 1", available_on: null,
    last_rent: null, link_id: "k1", link_rent: "1500.00", applications: 0, listing_id: "s1",
    description: null, listed_at: null, days_listed: null, photos: 0,
    details: { bedrooms: 1, bathrooms: "1", square_feet: null, market_rent: null },
    in_feed: false, feed_needs: [], ...extra,
  });

  it("says where a unit stands with the listing sites", () => {
    expect(feedStanding(unit({ in_feed: true }))).toEqual({ tone: "positive", label: "Sent" });
    expect(feedStanding(unit({}))).toEqual({ tone: "neutral", label: "Not sent" });
    expect(feedStanding(unit({ feed_needs: ["the unit's bedrooms"] }))).toEqual({ tone: "neutral", label: "Not sent" });
    // Sent, but its details cleared since: the feeds leave it out until it has them again.
    expect(feedStanding(unit({ in_feed: true, feed_needs: ["the unit's bedrooms"] }))).toEqual({
      tone: "caution", label: "Held back",
    });
  });

  it("reads a phone of ten digits as a phone, and leaves anything else as it is", () => {
    expect(feedPhone("2025550142")).toBe("(202) 555-0142");
    expect(feedPhone("555-0142")).toBe("555-0142");
  });
});
