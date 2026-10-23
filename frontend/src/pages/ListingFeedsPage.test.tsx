import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { Me } from "../api";
import type { FeedSettings } from "../leasing";
import { ListingFeedsPage } from "./ListingFeedsPage";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

const ME: Me = {
  kind: "staff", email: "staff@example.com", name: "Staff 1", person_id: "s1", pmc_id: "p1", pmc_name: "Synthetic PMC",
};
const ZILLOW: FeedSettings["feeds"][number] = {
  site: "zillow", takes: "Zillow, Trulia and HotPads: Zillow's rental listing feed", url: "https://pms.example.com/api/listings/p1/feeds/zillow.xml",
};
const MITS: FeedSettings["feeds"][number] = {
  site: "mits", takes: "Sites that take MITS (Property Marketing/ILS), Zillow's too", url: "https://pms.example.com/api/listings/p1/feeds/mits.xml",
};
const FEEDS = [ZILLOW, MITS];
const EMPTY: FeedSettings = {
  contact: null,
  feeds: FEEDS,
  missing: ["the company's mailing address (Settings, Company setup)", "a leasing contact's email and phone (here)"],
  listings: 0,
};

function server(first: FeedSettings, saved: FeedSettings) {
  let now = first;
  const fetch = vi.fn(async (_path: string, init?: RequestInit) => {
    if (init?.method === "POST") now = saved;
    return new Response(JSON.stringify(now), { status: 200 });
  });
  vi.stubGlobal("fetch", fetch);
  return fetch;
}

describe("ListingFeedsPage", () => {
  it("shows each feed's address to give its site, and what the feeds still need", async () => {
    server(EMPTY, EMPTY);
    render(<ListingFeedsPage me={ME} />);

    const feeds = await screen.findByRole("list", { name: "Feeds" });
    const zillow = within(feeds).getByRole("listitem", { name: ZILLOW.takes });
    expect(within(zillow).getByRole("link").getAttribute("href")).toBe(ZILLOW.url);
    expect(within(feeds).getAllByRole("listitem")).toHaveLength(2);
    expect(screen.getByText(
      "The feeds list nothing until the company has the company's mailing address (Settings, Company setup) and a leasing contact's email and phone (here).",
    )).toBeTruthy();
    expect(screen.getByText(/Now in the feeds: 0 listings\./)).toBeTruthy();
  });

  it("saves the leasing contact and reads it back, its phone as a phone", async () => {
    const saved: FeedSettings = {
      ...EMPTY,
      contact: { contact_name: "Leasing office", email: "leasing@example.com", phone: "2025550142" },
      missing: [],
      listings: 1,
    };
    const fetch = server(EMPTY, saved);
    render(<ListingFeedsPage me={ME} />);
    await screen.findByRole("list", { name: "Feeds" });

    fireEvent.change(screen.getByLabelText("Name (optional)"), { target: { value: " Leasing office " } });
    fireEvent.change(screen.getByLabelText("Email"), { target: { value: "leasing@example.com" } });
    fireEvent.change(screen.getByLabelText("Phone"), { target: { value: "(202) 555-0142" } });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    await screen.findByText("Saved the leasing contact.");

    const posts = fetch.mock.calls.filter(([, init]) => init?.method === "POST");
    expect(posts.map(([path, init]) => [path, JSON.parse(String(init?.body))])).toEqual([
      ["/api/leasing/feeds", { contact_name: "Leasing office", email: "leasing@example.com", phone: "(202) 555-0142" }],
    ]);
    expect(await screen.findByText(/Now in the feeds: 1 listing\./)).toBeTruthy();
    expect((screen.getByLabelText("Phone") as HTMLInputElement).value).toBe("(202) 555-0142");
    expect(screen.queryByText(/list nothing until/)).toBeNull();
  });

  it("copies a feed's address, or says it when the browser won't copy", async () => {
    server(EMPTY, EMPTY);
    const writeText = vi.fn().mockResolvedValueOnce(undefined).mockRejectedValueOnce(new Error("denied"));
    vi.stubGlobal("navigator", { clipboard: { writeText } });
    render(<ListingFeedsPage me={ME} />);
    const copy = await screen.findByRole("button", { name: `Copy the feed for ${MITS.takes}` });

    fireEvent.click(copy);
    await screen.findByText(`Copied the feed for ${MITS.takes}.`);
    fireEvent.click(copy);
    await screen.findByText(`The feed for ${MITS.takes}: ${MITS.url}`);

    expect(writeText).toHaveBeenCalledWith(MITS.url);
  });

  it("says why it couldn't read the feeds", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({ detail: "not allowed" }), { status: 403 })));
    render(<ListingFeedsPage me={ME} />);

    expect(await screen.findByText("not allowed")).toBeTruthy();
  });
});
