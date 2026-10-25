// A unit's details as the API sends them (migration 0124, src/pms/api/unit_details.py): its
// bedrooms, bathrooms (in halves, as text), square feet and market rent, each null when not
// recorded. Money stays text from the API to the screen.
export type UnitDetails = {
  bedrooms: number | null;
  bathrooms: string | null;
  square_feet: number | null;
  market_rent: string | null;
};

// "2 beds, 1.5 baths, 850 sq ft", "Studio, 1 bath"; what isn't recorded is left out, and a unit
// with none of the three reads "".
export function sizeLine(details: Pick<UnitDetails, "bedrooms" | "bathrooms" | "square_feet">): string {
  const parts: string[] = [];
  if (details.bedrooms !== null) {
    parts.push(details.bedrooms === 0 ? "Studio" : `${details.bedrooms} ${details.bedrooms === 1 ? "bed" : "beds"}`);
  }
  if (details.bathrooms !== null) parts.push(`${details.bathrooms} ${details.bathrooms === "1" ? "bath" : "baths"}`);
  if (details.square_feet !== null) parts.push(`${details.square_feet.toLocaleString("en-US")} sq ft`);
  return parts.join(", ");
}

// What a form sends: each field as typed, trimmed, without thousands commas or a dollar sign, an
// empty one as null (not recorded); the API reads the numbers and says why one is refused.
export type DetailsForm = { bedrooms: string; bathrooms: string; square_feet: string; market_rent: string };

export function detailsForm(details: UnitDetails): DetailsForm {
  return {
    bedrooms: details.bedrooms === null ? "" : String(details.bedrooms),
    bathrooms: details.bathrooms ?? "",
    square_feet: details.square_feet === null ? "" : String(details.square_feet),
    market_rent: details.market_rent ?? "",
  };
}

export function detailsBody(form: DetailsForm) {
  const text = (value: string) => value.replace(/[$,]/g, "").trim() || null;
  return {
    bedrooms: text(form.bedrooms),
    bathrooms: text(form.bathrooms),
    square_feet: text(form.square_feet),
    market_rent: text(form.market_rent),
  };
}
