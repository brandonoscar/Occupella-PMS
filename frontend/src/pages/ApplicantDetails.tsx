import type { Details, Household } from "../applying";
import { addressLine } from "../applying";
import { usDate } from "../money";

const LINK = "font-medium text-iris hover:text-iris-hover hover:underline";

// What an applicant gave of themselves with their application (ADR 0010 phase 2, part G), on
// their application's page and, once they are a tenant, on theirs. An application from before it
// asked, or made through the API without it, says it gave none.
export function DetailsList({ details, label }: { details: Details; label: string }) {
  const landlord = details.previous_landlord;
  const nothing = details.phone === null && details.emergency_contact === null && details.income_proofs.length === 0;
  if (nothing) return <p className="text-sm text-ink-subtle">No details came with this application.</p>;
  return (
    <dl className="grid grid-cols-[max-content_1fr] gap-x-6 gap-y-1 text-base" aria-label={label}>
      <dt className="text-ink-muted">Phone</dt>
      <dd>{details.phone ?? "Not given"}</dd>
      <dt className="text-ink-muted">Lived at</dt>
      <dd>{details.address ? addressLine(details.address) : "Not given"}</dd>
      <dt className="text-ink-muted">Previous landlord</dt>
      <dd>
        {landlord === null ? "None given" : [
          landlord.name,
          landlord.phone,
          landlord.from_on || landlord.to_on
            ? `${landlord.from_on ? usDate(landlord.from_on) : "?"} – ${landlord.to_on ? usDate(landlord.to_on) : "?"}`
            : null,
        ].filter(Boolean).join(", ")}
      </dd>
      <dt className="text-ink-muted">Employer</dt>
      <dd>{details.employer ?? "None given"}</dd>
      <dt className="text-ink-muted">Emergency contact</dt>
      <dd>{details.emergency_contact ? `${details.emergency_contact.name}, ${details.emergency_contact.phone}` : "None given"}</dd>
      <dt className="text-ink-muted">Proof of income</dt>
      <dd>
        {details.income_proofs.length === 0 ? "None given" : details.income_proofs.map((doc, n) => (
          <a key={doc} href={`/api/documents/${doc}/file`} className={`${LINK} mr-3`}>
            {details.income_proofs.length > 1 ? `Proof of income ${n + 1}` : "Proof of income"}
          </a>
        ))}
      </dd>
    </dl>
  );
}

export function HouseholdList({ household }: { household: Household }) {
  const empty = household.occupants.length + household.pets.length + household.vehicles.length === 0;
  if (empty) return <p className="text-sm text-ink-subtle">No one else, no animals and no vehicles listed.</p>;
  return (
    <dl className="grid grid-cols-[max-content_1fr] gap-x-6 gap-y-1 text-base" aria-label="Household">
      <dt className="text-ink-muted">Others living there</dt>
      <dd>{household.occupants.length ? household.occupants.join(", ") : "None"}</dd>
      <dt className="text-ink-muted">Animals</dt>
      <dd>
        {household.pets.length === 0 ? "None" : (
          <ul aria-label="Animals">
            {household.pets.map((p, n) => (
              <li key={n}>
                {[p.name, p.kind, p.breed, p.weight_lbs && `${p.weight_lbs} lbs`].filter(Boolean).join(", ")}
                {p.assistance_animal && " (assistance animal: no pet rent, fee or deposit)"}
              </li>
            ))}
          </ul>
        )}
      </dd>
      <dt className="text-ink-muted">Vehicles</dt>
      <dd>
        {household.vehicles.length === 0 ? "None" : (
          <ul aria-label="Vehicles">
            {household.vehicles.map((v, n) => (
              <li key={n}>
                {[v.color, v.make, v.model].filter(Boolean).join(" ")}, {v.plate}{v.plate_state ? ` (${v.plate_state})` : ""}
              </li>
            ))}
          </ul>
        )}
      </dd>
    </dl>
  );
}
