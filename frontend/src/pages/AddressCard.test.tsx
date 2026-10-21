import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { Location, PropertyAddress } from "../rentals";
import { AddressCard } from "./AddressCard";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

const ADDRESS: PropertyAddress = {
  line_1: "100 Synthetic Way", line_2: "Building B", city: "Demo City", state: "CA", zip: "00001",
  one_line: "100 Synthetic Way, Building B, Demo City, CA 00001", recorded_by: "Staff 1",
  recorded_at: "2026-10-21T03:00:00Z",
};
const LOCATION: Location = {
  latitude: "37.123456", longitude: "-120.123457", matched: "100 SYNTHETIC WAY, DEMO CITY, CA, 00001",
  geocoder: "census", map_url: "https://www.openstreetmap.org/?mlat=37.123456&mlon=-120.123457#map=17/37.123456/-120.123457",
  found_at: "2026-10-21T03:01:00Z",
};

// POSTs answer 204 (an address) or 201 (a location), or refuse with `refuse` when given.
function server(refuse = "") {
  const fetch = vi.fn(async (_path: string, init?: RequestInit) => {
    if (refuse) return new Response(JSON.stringify({ detail: refuse }), { status: 422 });
    if (init?.method === "POST" && String(_path).endsWith("/locate")) {
      return new Response(JSON.stringify(LOCATION), { status: 201 });
    }
    return new Response(null, { status: 204 });
  });
  vi.stubGlobal("fetch", fetch);
  return fetch;
}

const posted = (fetch: ReturnType<typeof server>) =>
  fetch.mock.calls.map(([path, init]) => [path, init?.body ? JSON.parse(String(init.body)) : null]);

describe("AddressCard", () => {
  it("shows the address and where it is, with OpenStreetMap at the point", () => {
    render(<AddressCard propertyId="p1" address={ADDRESS} location={LOCATION} canLocate onChange={() => undefined} />);

    expect(screen.getByTestId("property-address").textContent).toBe("100 Synthetic WayBuilding BDemo City, CA 00001");
    expect(screen.getByTestId("property-location").textContent).toBe(
      "On the map at 37.123456, -120.123457, as the geocoder matched it: 100 SYNTHETIC WAY, DEMO CITY, CA, 00001. Open in OpenStreetMap",
    );
    const map = screen.getByRole("link", { name: "Open in OpenStreetMap" });
    expect(map.getAttribute("href")).toBe(LOCATION.map_url);
    expect(map.getAttribute("rel")).toBe("noopener noreferrer");
    expect(map.getAttribute("target")).toBe("_blank");
  });

  it("adds an address in a side panel, a blank second line left out", async () => {
    const fetch = server();
    const onChange = vi.fn();
    render(<AddressCard propertyId="p1" address={null} location={null} canLocate onChange={onChange} />);
    expect(screen.getByText("No address yet.")).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Find on map" })).toBeNull();

    fireEvent.click(screen.getByRole("button", { name: "Add address" }));
    const panel = screen.getByRole("dialog", { name: "Add address" });
    fireEvent.change(within(panel).getByLabelText("Street"), { target: { value: "100 Synthetic Way" } });
    fireEvent.change(within(panel).getByLabelText("City"), { target: { value: "Demo City" } });
    fireEvent.change(within(panel).getByLabelText("State (two letters)"), { target: { value: "ca" } });
    fireEvent.change(within(panel).getByLabelText("ZIP code"), { target: { value: "00001" } });
    fireEvent.click(within(panel).getByRole("button", { name: "Save address" }));

    expect(await screen.findByText("Address saved.")).toBeTruthy();
    expect(posted(fetch)).toEqual([[
      "/api/properties/p1/address",
      { line_1: "100 Synthetic Way", line_2: null, city: "Demo City", state: "ca", zip: "00001" },
    ]]);
    expect(onChange).toHaveBeenCalled();
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("edits the address it has, and finds it on a map when the server can", async () => {
    const fetch = server();
    render(<AddressCard propertyId="p1" address={ADDRESS} location={null} canLocate onChange={() => undefined} />);
    expect(screen.getByText("Not found on a map yet.")).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: "Find on map" }));
    expect(await screen.findByText("Found on the map.")).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: "Edit address" }));
    const panel = screen.getByRole("dialog", { name: "Edit address" });
    expect((within(panel).getByLabelText("Building or unit (optional)") as HTMLInputElement).value).toBe("Building B");
    fireEvent.change(within(panel).getByLabelText("Street"), { target: { value: "200 Example Street" } });
    fireEvent.click(within(panel).getByRole("button", { name: "Save address" }));
    expect(await screen.findByText("Address saved.")).toBeTruthy();

    expect(posted(fetch)).toEqual([
      ["/api/properties/p1/locate", {}],
      ["/api/properties/p1/address", {
        line_1: "200 Example Street", line_2: "Building B", city: "Demo City", state: "CA", zip: "00001",
      }],
    ]);
  });

  it("offers no map without a geocoder, and says why one was refused", async () => {
    render(<AddressCard propertyId="p1" address={ADDRESS} location={null} canLocate={false} onChange={() => undefined} />);
    expect(screen.queryByRole("button", { name: "Find on map" })).toBeNull();
    expect(screen.queryByText("Not found on a map yet.")).toBeNull();
    cleanup();

    server("the geocoder found no match for 1 Nowhere Lane, Demo City, CA 00001: check the address");
    render(<AddressCard propertyId="p1" address={ADDRESS} location={null} canLocate onChange={() => undefined} />);
    fireEvent.click(screen.getByRole("button", { name: "Find on map" }));
    expect(await screen.findByText(/found no match for 1 Nowhere Lane/)).toBeTruthy();
  });
});
