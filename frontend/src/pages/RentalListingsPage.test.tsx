import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { Me } from "../api";
import { holdBack } from "../holdBack";
import type { OpenUnit } from "../leasing";
import { RentalListingsPage, daysListed, publicPage } from "./RentalListingsPage";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  localStorage.clear();
});

const ME: Me = {
  kind: "staff", email: "staff@example.com", name: "Staff 1", person_id: "s1", pmc_id: "p1", pmc_name: "Synthetic PMC",
};
const VACANT: OpenUnit = {
  unit_id: "u1", property_id: "pr1", property: "Property 1", unit: "Unit 1", available_on: "2026-11-01",
  last_rent: "1400.00", link_id: null, link_rent: null, applications: 0, listing_id: null, description: null,
  listed_at: null, days_listed: null, photos: 0,
  details: { bedrooms: 0, bathrooms: "1", square_feet: 450, market_rent: "1450.00" },
  in_feed: false, feed_needs: [],
};
const LISTED: OpenUnit = {
  ...VACANT, unit_id: "u2", unit: "Unit 2", available_on: null, link_id: "k2", link_rent: "1550.00",
  applications: 3, listing_id: "s2", description: "Corner unit.", listed_at: "2026-10-09T15:00:00Z",
  days_listed: 10, photos: 2, details: { bedrooms: null, bathrooms: null, square_feet: null, market_rent: null },
  feed_needs: ["the unit's bedrooms", "the unit's bathrooms"],
};
const LINKED: OpenUnit = { ...VACANT, unit_id: "u3", unit: "Unit 3", link_id: "k3", link_rent: "1600.00" };

function server(units: OpenUnit[] = [VACANT, LISTED, LINKED]) {
  const fetch = vi.fn(async (path: string, init?: RequestInit) => {
    if (init?.method === "POST") {
      const answer = path === "/api/leasing/links" ? { id: "k9", url: "http://x/apply#token-9", listing_id: null } : {};
      return new Response(JSON.stringify(answer), { status: 201 });
    }
    if (path.startsWith("/api/leasing/vacancies?")) {
      return new Response(JSON.stringify({ items: units, total: units.length, page: 1, per_page: 50 }));
    }
    return new Response("[]");
  });
  vi.stubGlobal("fetch", fetch);
  return fetch;
}

const posted = (fetch: ReturnType<typeof server>) =>
  fetch.mock.calls.filter(([, init]) => init?.method === "POST").map(([path, init]) => [path, JSON.parse(String(init?.body))]);

describe("RentalListingsPage", () => {
  it("lists each unit coming free with its rent, applications and whether it is listed", async () => {
    server();
    render(<RentalListingsPage me={ME} />);

    const rows = await screen.findAllByRole("row", { name: /^Property 1, Unit/ });
    expect(rows.map((row) => within(row).getAllByRole("cell").slice(0, 6).map((c) => c.textContent))).toEqual([
      ["Property 1, Unit 1Studio, 1 bath, 450 sq ft", "11/01/2026", "$1,400.00", "$1,450.00", "0", "Not listed"],
      ["Property 1, Unit 2", "Leased", "$1,550.00", "", "3", "Listed"],
      ["Property 1, Unit 3Studio, 1 bath, 450 sq ft", "11/01/2026", "$1,600.00", "$1,450.00", "0", "Not listed"],
    ]);
    // How long each listing has been up, and how many photos its unit has.
    expect(rows.map((row) => within(row).getAllByRole("cell").slice(7, 9).map((c) => c.textContent))).toEqual([
      ["", "0"], ["10 days", "2"], ["", "0"],
    ]);
    expect(screen.getByRole("link", { name: "Property 1, Unit 1" }).getAttribute("href")).toBe("/units/u1");
  });

  it("lists a unit with no link by opening one at the rent asked, and one with a link on it", async () => {
    const fetch = server();
    render(<RentalListingsPage me={ME} />);
    const first = await screen.findByRole("row", { name: "Property 1, Unit 1" });

    fireEvent.click(within(first).getByRole("button", { name: "List" }));
    let panel = screen.getByRole("dialog", { name: "List on the public page" });
    expect((within(panel).getByLabelText("Rent a month") as HTMLInputElement).value).toBe("1400.00");
    fireEvent.change(within(panel).getByLabelText("Rent a month"), { target: { value: "1450.00" } });
    fireEvent.change(within(panel).getByLabelText("Description (optional)"), { target: { value: " Sunny. " } });
    fireEvent.click(within(panel).getByRole("button", { name: "List it" }));
    await screen.findByText("Listed Property 1, Unit 1 at $1,450.00.");

    fireEvent.click(within(screen.getByRole("row", { name: "Property 1, Unit 3" })).getByRole("button", { name: "List" }));
    panel = screen.getByRole("dialog", { name: "List on the public page" });
    expect(within(panel).queryByLabelText("Rent a month")).toBeNull();
    fireEvent.click(within(panel).getByRole("button", { name: "List it" }));
    await screen.findByText("Listed Property 1, Unit 3 at $1,600.00.");

    expect(posted(fetch)).toEqual([
      ["/api/leasing/links", { unit_id: "u1", monthly_rent: "1450.00", listing: { description: "Sunny." } }],
      ["/api/leasing/links/k3/listing", { description: null }],
    ]);
  });

  it("unlists a listed unit, and closes a link from the ⋯ menu once confirmed", async () => {
    const fetch = server();
    render(<RentalListingsPage me={ME} />);
    const listed = await screen.findByRole("row", { name: "Property 1, Unit 2" });

    fireEvent.click(within(listed).getByRole("button", { name: "Unlist" }));
    await screen.findByText("Took Property 1, Unit 2 off the public page.");
    fireEvent.click(within(listed).getByRole("button", { name: "Actions for Property 1, Unit 2" }));
    fireEvent.click(screen.getByRole("menuitem", { name: "Close its link" }));
    expect(screen.getByRole("alertdialog").textContent).toContain(
      "Close the application link for Property 1, Unit 2? It takes no more applications and leaves the public page.",
    );
    fireEvent.click(within(screen.getByRole("alertdialog")).getByRole("button", { name: "Close its link" }));
    await screen.findByText("Closed the link for Property 1, Unit 2.");

    expect(posted(fetch)).toEqual([["/api/leasing/listings/s2/remove", {}], ["/api/leasing/links/k2/close", {}]]);
  });

  it("says where each listing stands with the listing sites, and sends one or stops sending it", async () => {
    const SENT: OpenUnit = { ...LINKED, unit_id: "u4", unit: "Unit 4", listing_id: "s4", in_feed: true };
    const fetch = server([VACANT, LISTED, SENT]);
    render(<RentalListingsPage me={ME} />);
    const listed = await screen.findByRole("row", { name: "Property 1, Unit 2" });
    const sent = screen.getByRole("row", { name: "Property 1, Unit 4" });

    expect(within(listed).getAllByRole("cell").at(6)?.textContent).toBe(
      "Not sentNeeds the unit's bedrooms, the unit's bathrooms",
    );
    expect(within(sent).getAllByRole("cell").at(6)?.textContent).toBe("Sent");
    // A unit not on the public page has nothing to send.
    expect(within(screen.getByRole("row", { name: "Property 1, Unit 1" })).getAllByRole("cell").at(6)?.textContent).toBe("");
    fireEvent.click(within(listed).getByRole("button", { name: "Actions for Property 1, Unit 2" }));
    fireEvent.click(screen.getByRole("menuitem", { name: "Send to listing sites" }));
    await screen.findByText("Sent Property 1, Unit 2 to the listing sites' feeds.");
    fireEvent.click(within(sent).getByRole("button", { name: "Actions for Property 1, Unit 4" }));
    fireEvent.click(screen.getByRole("menuitem", { name: "Stop sending to listing sites" }));
    await screen.findByText("Took Property 1, Unit 4 out of the listing sites' feeds.");

    expect(posted(fetch)).toEqual([
      ["/api/leasing/listings/s2/feed", { in_feed: true }],
      ["/api/leasing/listings/s4/feed", { in_feed: false }],
    ]);
    expect(screen.getByRole("link", { name: "Listing feeds" }).getAttribute("href")).toBe("/settings/listing-feeds");
  });

  it("opens an application link and shows it once", async () => {
    const fetch = server();
    render(<RentalListingsPage me={ME} />);
    const row = await screen.findByRole("row", { name: "Property 1, Unit 1" });

    fireEvent.click(within(row).getByRole("button", { name: "Actions for Property 1, Unit 1" }));
    fireEvent.click(screen.getByRole("menuitem", { name: "Open an application link" }));
    const panel = screen.getByRole("dialog", { name: "Open an application link" });
    fireEvent.click(within(panel).getByRole("button", { name: "Open the link" }));

    expect((await within(panel).findByTestId("application-link")).textContent).toBe("http://x/apply#token-9");
    expect(posted(fetch)).toEqual([["/api/leasing/links", { unit_id: "u1", monthly_rent: "1400.00" }]]);
  });

  it("copies the public page's link, or says it when the browser won't copy, after what a save said", async () => {
    server();
    const writeText = vi.fn().mockResolvedValueOnce(undefined).mockRejectedValueOnce(new Error("denied"));
    vi.stubGlobal("navigator", { clipboard: { writeText } });
    render(<RentalListingsPage me={ME} />);
    const listed = await screen.findByRole("row", { name: "Property 1, Unit 2" });
    fireEvent.click(within(listed).getByRole("button", { name: "Unlist" }));
    await screen.findByText("Took Property 1, Unit 2 off the public page.");

    fireEvent.click(screen.getByRole("button", { name: "Copy public page link" }));
    await screen.findByText("Copied the public page's link.");
    fireEvent.click(screen.getByRole("button", { name: "Copy public page link" }));
    await screen.findByText(`The public page's link: ${publicPage("p1")}`);

    expect(writeText).toHaveBeenCalledWith(`${window.location.origin}/listings?pmc=p1`);
    expect(screen.getByRole("link", { name: "Public page" }).getAttribute("href")).toBe("/listings?pmc=p1");
  });

  it("says when no unit comes free", async () => {
    server([]);
    render(<RentalListingsPage me={ME} />);

    await screen.findByText("No unit comes free in the next 90 days.");
  });

  it("keeps a unit's listing photos: shows them, adds a JPEG or PNG and removes one", async () => {
    const photos = [
      { id: "d1", title: "Kitchen", uploaded_at: "2026-10-09T15:00:00Z" },
      { id: "d2", title: "Porch", uploaded_at: "2026-10-09T15:01:00Z" },
    ];
    const fetch = vi.fn(async (path: string, init?: RequestInit) => {
      if (init?.method === "POST") return new Response(JSON.stringify({ id: "d3" }), { status: 201 });
      if (path === "/api/leasing/units/u2/photos") return new Response(JSON.stringify(photos));
      return new Response(JSON.stringify({ items: [VACANT, LISTED], total: 2, page: 1, per_page: 50 }));
    });
    vi.stubGlobal("fetch", fetch);
    render(<RentalListingsPage me={ME} />);
    const row = await screen.findByRole("row", { name: "Property 1, Unit 2" });

    fireEvent.click(within(row).getByRole("button", { name: "Photos, Property 1, Unit 2" }));
    const panel = screen.getByRole("dialog", { name: "Listing photos" });
    const shown = await within(panel).findByRole("list", { name: "Listing photos, Property 1, Unit 2" });
    expect(within(shown).getAllByRole("img").map((img) => [img.getAttribute("alt"), img.getAttribute("src")])).toEqual([
      ["Kitchen", "/api/documents/d1/file"], ["Porch", "/api/documents/d2/file"],
    ]);

    const picker = within(panel).getByLabelText("Add a photo (JPEG or PNG)");
    fireEvent.change(picker, { target: { files: [new File(["%PDF-"], "lease.pdf", { type: "application/pdf" })] } });
    expect(await within(panel).findByText("A photo is a JPEG or a PNG.")).toBeTruthy();
    const png = new File([new Uint8Array([0x89, 0x50, 0x4e, 0x47])], "Living room.png", { type: "image/png" });
    fireEvent.change(picker, { target: { files: [png] } });
    await within(panel).findByText("Added Living room.");
    fireEvent.click(within(panel).getByRole("button", { name: "Remove Porch" }));
    await within(panel).findByText("Removed Porch.");

    const sent = fetch.mock.calls.filter(([, init]) => init?.method === "POST").map(([path, init]) => [path, JSON.parse(String(init?.body))]);
    expect(sent).toEqual([
      ["/api/documents", { kind: "listing_photo", about_id: "u2", title: "Living room", content_type: "image/png", content_base64: "iVBORw==" }],
      ["/api/documents/d2/remove", {}],
    ]);
    // Each change reads the photos and the list again; the last reads start after "Removed Porch."
    // shows, so wait for them.
    await waitFor(() => {
      expect(fetch.mock.calls.filter(([path]) => path === "/api/leasing/units/u2/photos").length).toBe(3);
      expect(fetch.mock.calls.filter(([path]) => path.startsWith("/api/leasing/vacancies?")).length).toBe(3);
    });
  });

  it("shows the units of the property group picked now, even when an earlier group's answer comes back last", async () => {
    const groups = { groups: [{ id: "g1", name: "North", properties: [] }, { id: "g2", name: "South", properties: [] }], properties: [] };
    vi.stubGlobal("fetch", vi.fn(async (path: string) => {
      if (path === "/api/property-groups") return new Response(JSON.stringify(groups));
      const units = path.includes("group=g1") ? [VACANT] : path.includes("group=g2") ? [LISTED] : [LINKED];
      return new Response(JSON.stringify({ items: units, total: units.length, page: 1, per_page: 50 }));
    }));
    const release = holdBack((path) => path.includes("group=g1"));
    render(<RentalListingsPage me={ME} />);
    await screen.findByRole("row", { name: "Property 1, Unit 3" });
    const picker = screen.getByRole("combobox", { name: "Properties shown" });
    await within(picker).findByRole("option", { name: "South" });

    fireEvent.change(picker, { target: { value: "group:g1" } });
    fireEvent.change(picker, { target: { value: "group:g2" } });
    await screen.findByRole("row", { name: "Property 1, Unit 2" });
    await release();

    expect(screen.getByRole("row", { name: "Property 1, Unit 2" })).toBeTruthy();
    expect(screen.queryByRole("row", { name: "Property 1, Unit 1" })).toBeNull();
  });

  it("says how many days a listing has been up", () => {
    expect([daysListed(null), daysListed(1), daysListed(2)]).toEqual(["", "1 day", "2 days"]);
  });

  it("builds the public page's address from where the portal is served", () => {
    expect(publicPage("p1", "https://pms.example.com")).toBe("https://pms.example.com/listings?pmc=p1");
  });
});
