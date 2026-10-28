import { useState } from "react";
import type { ApplicantDraft, HouseholdDraft, PetDraft, VehicleDraft } from "../applying";
import { MOST, blankPet, blankVehicle } from "../applying";
import { Button } from "../components/Button";
import { Field } from "../components/Field";
import { Notice } from "../components/Notice";
import { MAX_BYTES, toBase64, typeOf } from "../documents";

// The application form's questions about each applicant and the household (ADR 0010 phase 2,
// part G): what applying.ts says, and no more.

const GROUP = "flex flex-col gap-3 rounded-sm border border-line px-4 py-3";
const LEGEND = "px-1 text-sm text-ink-subtle";

export function ApplicantFields({ n, applicant, change }: {
  n: number;
  applicant: ApplicantDraft;
  change: (part: Partial<ApplicantDraft>) => void;
}) {
  const [problem, setProblem] = useState("");
  const field = (key: keyof ApplicantDraft) => ({
    value: String(applicant[key] ?? ""),
    onChange: (e: React.ChangeEvent<HTMLInputElement>) => change({ [key]: e.target.value }),
  });
  return (
    <>
      <Field label="Phone" id={`phone-${n}`} type="tel" required autoComplete="tel" {...field("phone")} />
      <fieldset className={GROUP}>
        <legend className={LEGEND}>Where you live now</legend>
        <Field label="Street address" id={`line-1-${n}`} required autoComplete="address-line1" {...field("line_1")} />
        <Field label="Apartment or unit (if any)" id={`line-2-${n}`} autoComplete="address-line2" {...field("line_2")} />
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
          <Field label="City" id={`city-${n}`} required autoComplete="address-level2" {...field("city")} />
          <Field label="State" id={`state-${n}`} required autoComplete="address-level1" {...field("state")} />
          <Field label="ZIP code" id={`zip-${n}`} required autoComplete="postal-code" {...field("postal_code")} />
        </div>
      </fieldset>
      <fieldset className={GROUP}>
        <legend className={LEGEND}>Your previous landlord, if you had one</legend>
        <Field label="Previous landlord's name" id={`landlord-${n}`} {...field("landlord")} />
        <Field label="Previous landlord's phone" id={`landlord-phone-${n}`} type="tel" disabled={!applicant.landlord.trim()}
          {...field("landlord_phone")} />
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
          <Field label="Rented from" id={`rented-from-${n}`} type="date" disabled={!applicant.landlord.trim()}
            {...field("rented_from")} />
          <Field label="Rented to" id={`rented-to-${n}`} type="date" disabled={!applicant.landlord.trim()}
            {...field("rented_to")} />
        </div>
      </fieldset>
      <Field label="Employer, if you have one" id={`employer-${n}`} autoComplete="organization" {...field("employer")} />
      <fieldset className={GROUP}>
        <legend className={LEGEND}>Emergency contact</legend>
        <Field label="Emergency contact's name" id={`emergency-name-${n}`} required {...field("emergency_name")} />
        <Field label="Emergency contact's phone" id={`emergency-phone-${n}`} type="tel" required
          {...field("emergency_phone")} />
      </fieldset>
      <label htmlFor={`proof-${n}`} className="flex flex-col gap-1.5 text-sm text-ink-muted">
        Proof of income: a pay stub, an offer letter or a bank statement (PDF, JPEG or PNG, at most 5 MB)
        <input id={`proof-${n}`} type="file" accept=".pdf,.jpg,.jpeg,.png" className="text-sm text-ink"
          onChange={async (e) => {
            const chosen = e.target.files?.[0];
            setProblem("");
            if (!chosen) return change({ proof: null });
            const type = typeOf(chosen);
            if (type === null) {
              setProblem("A proof of income is a PDF, a JPEG or a PNG.");
              return change({ proof: null });
            }
            if (chosen.size > MAX_BYTES) {
              setProblem("A proof of income is a file of at most 5 MB.");
              return change({ proof: null });
            }
            const bytes = new Uint8Array(await chosen.arrayBuffer());
            change({ proof: { content_type: type, content_base64: toBase64(bytes), name: chosen.name } });
          }} />
      </label>
      <Notice tone="error">{problem}</Notice>
    </>
  );
}

export function HouseholdFields({ household, setHousehold }: {
  household: HouseholdDraft;
  setHousehold: (next: HouseholdDraft) => void;
}) {
  const occupant = (i: number, name: string) =>
    setHousehold({ ...household, occupants: household.occupants.map((o, j) => (j === i ? name : o)) });
  const pet = (i: number, part: Partial<PetDraft>) =>
    setHousehold({ ...household, pets: household.pets.map((p, j) => (j === i ? { ...p, ...part } : p)) });
  const vehicle = (i: number, part: Partial<VehicleDraft>) =>
    setHousehold({ ...household, vehicles: household.vehicles.map((v, j) => (j === i ? { ...v, ...part } : v)) });
  const drop = <K extends keyof HouseholdDraft>(key: K, i: number) =>
    setHousehold({ ...household, [key]: household[key].filter((_, j) => j !== i) });
  return (
    <fieldset className="flex flex-col gap-4 border-t border-line pt-4">
      <legend className="text-sm text-ink-subtle">Your household</legend>
      <fieldset className={GROUP} aria-label="Others who would live there">
        <legend className={LEGEND}>Others who would live there, who aren't applying</legend>
        {household.occupants.map((name, i) => (
          <div key={i} className="flex items-end gap-2">
            <Field label={`Name of person ${i + 1}`} id={`occupant-${i}`} required value={name}
              onChange={(e) => occupant(i, e.target.value)} />
            <Button type="button" variant="ghost" size="sm" onClick={() => drop("occupants", i)}>Remove</Button>
          </div>
        ))}
        {household.occupants.length < MOST && (
          <div>
            <Button type="button" variant="ghost" size="sm"
              onClick={() => setHousehold({ ...household, occupants: [...household.occupants, ""] })}>
              Add someone
            </Button>
          </div>
        )}
      </fieldset>
      <fieldset className={GROUP} aria-label="Pets and assistance animals">
        <legend className={LEGEND}>Pets and assistance animals</legend>
        {household.pets.map((p, i) => (
          <div key={i} className="flex flex-col gap-3 border-b border-line pb-3">
            <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
              <Field label="Animal's name" id={`pet-name-${i}`} required value={p.name}
                onChange={(e) => pet(i, { name: e.target.value })} />
              <Field label="Kind (dog, cat...)" id={`pet-kind-${i}`} required value={p.kind}
                onChange={(e) => pet(i, { kind: e.target.value })} />
              <Field label="Breed (if known)" id={`pet-breed-${i}`} value={p.breed}
                onChange={(e) => pet(i, { breed: e.target.value })} />
              <Field label="Weight in pounds (if known)" id={`pet-weight-${i}`} inputMode="decimal" value={p.weight_lbs}
                onChange={(e) => pet(i, { weight_lbs: e.target.value })} />
            </div>
            <label className="flex items-center gap-2 text-sm text-ink">
              <input type="checkbox" checked={p.assistance_animal}
                onChange={(e) => pet(i, { assistance_animal: e.target.checked })} />
              An assistance animal (a service or support animal): never charged pet rent, a pet fee or a pet deposit
            </label>
            <div>
              <Button type="button" variant="ghost" size="sm" onClick={() => drop("pets", i)}>Remove</Button>
            </div>
          </div>
        ))}
        {household.pets.length < MOST && (
          <div>
            <Button type="button" variant="ghost" size="sm"
              onClick={() => setHousehold({ ...household, pets: [...household.pets, blankPet()] })}>
              Add an animal
            </Button>
          </div>
        )}
      </fieldset>
      <fieldset className={GROUP} aria-label="Vehicles">
        <legend className={LEGEND}>Vehicles</legend>
        {household.vehicles.map((v, i) => (
          <div key={i} className="flex flex-col gap-3 border-b border-line pb-3">
            <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
              <Field label="Make" id={`vehicle-make-${i}`} required value={v.make}
                onChange={(e) => vehicle(i, { make: e.target.value })} />
              <Field label="Model (if any)" id={`vehicle-model-${i}`} value={v.model}
                onChange={(e) => vehicle(i, { model: e.target.value })} />
              <Field label="Color (if any)" id={`vehicle-color-${i}`} value={v.color}
                onChange={(e) => vehicle(i, { color: e.target.value })} />
              <Field label="License plate" id={`vehicle-plate-${i}`} required maxLength={15} value={v.plate}
                onChange={(e) => vehicle(i, { plate: e.target.value })} />
              <Field label="Plate's state (if any)" id={`vehicle-plate-state-${i}`} value={v.plate_state}
                onChange={(e) => vehicle(i, { plate_state: e.target.value })} />
            </div>
            <div>
              <Button type="button" variant="ghost" size="sm" onClick={() => drop("vehicles", i)}>Remove</Button>
            </div>
          </div>
        ))}
        {household.vehicles.length < MOST && (
          <div>
            <Button type="button" variant="ghost" size="sm"
              onClick={() => setHousehold({ ...household, vehicles: [...household.vehicles, blankVehicle()] })}>
              Add a vehicle
            </Button>
          </div>
        )}
      </fieldset>
    </fieldset>
  );
}
