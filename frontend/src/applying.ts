import type { ContentType } from "./documents";

// What the application form asks (ADR 0010 phase 2, part G; src/pms/api/applicant_details.py):
// each applicant's phone, current address, previous landlord and employer if they have them, an
// emergency contact and a proof of income; and the household's other occupants, pets (an
// assistance animal marked: never charged pet rent, a fee or a deposit) and vehicles. Nothing
// else: no question about anything fair housing protects, and never a Social Security number or
// a date of birth (the screening company asks for those itself).

export type ApplicantDraft = {
  name: string;
  email: string;
  phone: string;
  line_1: string;
  line_2: string;
  city: string;
  state: string;
  postal_code: string;
  landlord: string;
  landlord_phone: string;
  rented_from: string;
  rented_to: string;
  employer: string;
  emergency_name: string;
  emergency_phone: string;
  proof: { content_type: ContentType; content_base64: string; name: string } | null;
};
export type PetDraft = { name: string; kind: string; breed: string; weight_lbs: string; assistance_animal: boolean };
export type VehicleDraft = { make: string; model: string; color: string; plate: string; plate_state: string };
export type HouseholdDraft = { occupants: string[]; pets: PetDraft[]; vehicles: VehicleDraft[] };

export const MOST = 10;

export function blankApplicant(): ApplicantDraft {
  return {
    name: "", email: "", phone: "", line_1: "", line_2: "", city: "", state: "", postal_code: "",
    landlord: "", landlord_phone: "", rented_from: "", rented_to: "", employer: "",
    emergency_name: "", emergency_phone: "", proof: null,
  };
}
export const blankPet = (): PetDraft => ({ name: "", kind: "", breed: "", weight_lbs: "", assistance_animal: false });
export const blankVehicle = (): VehicleDraft => ({ make: "", model: "", color: "", plate: "", plate_state: "" });
export const blankHousehold = (): HouseholdDraft => ({ occupants: [], pets: [], vehicles: [] });

// An optional answer left blank is left out.
const given = (text: string): string | undefined => text.trim() || undefined;

/** An applicant as /api/apply takes them. */
export function applicantBody(a: ApplicantDraft) {
  return {
    name: a.name,
    email: a.email,
    details: {
      phone: a.phone,
      address: { line_1: a.line_1, line_2: given(a.line_2), city: a.city, state: a.state, postal_code: a.postal_code },
      previous_landlord: given(a.landlord) && {
        name: a.landlord,
        phone: given(a.landlord_phone),
        from_on: given(a.rented_from),
        to_on: given(a.rented_to),
      },
      employer: given(a.employer),
      emergency_contact: { name: a.emergency_name, phone: a.emergency_phone },
      income_proof: a.proof ? { content_type: a.proof.content_type, content_base64: a.proof.content_base64 } : undefined,
    },
  };
}

/** The household as /api/apply takes it: names left blank are left out. */
export function householdBody(h: HouseholdDraft) {
  return {
    occupants: h.occupants.map((name) => name.trim()).filter(Boolean),
    pets: h.pets.map((p) => ({
      name: p.name, kind: p.kind, breed: given(p.breed), weight_lbs: given(p.weight_lbs), assistance_animal: p.assistance_animal,
    })),
    vehicles: h.vehicles.map((v) => ({
      make: v.make, model: given(v.model), color: given(v.color), plate: v.plate, plate_state: given(v.plate_state),
    })),
  };
}

/** What staff see of an applicant's details (src/pms/api/applicant_details.py, Details). */
export type Details = {
  phone: string | null;
  address: { line_1: string; line_2: string | null; city: string; state: string; postal_code: string } | null;
  previous_landlord: { name: string; phone: string | null; from_on: string | null; to_on: string | null } | null;
  employer: string | null;
  emergency_contact: { name: string; phone: string; recorded_at: string } | null;
  income_proofs: string[];
};
export type Household = {
  occupants: string[];
  pets: { name: string; kind: string; breed: string | null; weight_lbs: string | null; assistance_animal: boolean }[];
  vehicles: { make: string; model: string | null; color: string | null; plate: string; plate_state: string | null }[];
};

/** An address on one line: 1 Sample Street, Apt 2, Sampleton, ST 00001. */
export function addressLine(a: NonNullable<Details["address"]>): string {
  return [a.line_1, a.line_2, a.city, `${a.state} ${a.postal_code}`].filter(Boolean).join(", ");
}

/** A tenant's emergency contact, and the application they became a tenant through (GET
 * /api/tenants/<id>/application). */
export type FromApplication = {
  emergency_contact: { name: string; phone: string; recorded_at: string } | null;
  application: { application_id: string; submitted_at: string; details: Details } | null;
};
