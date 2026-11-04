# ADR 0012: The portals' second version

Decided by the founder on 2026-11-03 ("C", for review that night), under ADR 0002 (the owner and
tenant portals) and ADR 0009 (announcements; what Buildium has, we have).

## Context

The owner and tenant portals were one long page each. An owner saw their money, statements and
bills, but not their units, who rents them, what is vacant or the repairs at their properties,
and could not change their own contact details. A tenant could not tell the company they are
leaving, write to it, read what it announces or send it a document. Card payments in the portal
are lane 1 of ADR 0009 (Stripe Connect) and stay there.

## Decisions

1. **Sections.** Each portal is in sections in its sidebar, each a page of its own reading the
   same API:
   - an owner's: Overview, Properties (each property at `/properties/<id>`), Statements and
     reports, Bills, Repairs, Documents, Messages and Profile;
   - a tenant's: Overview, Payments, Lease, Repairs, Messages and Profile.
2. **An owner's properties.** Each opens to its units: who rents each (tenants' names only,
   never their contact details), the rent, what each lease owes today, what is vacant and since
   when, and who gave notice to move out. Repairs lists the work orders at their properties with
   their status, vendor, visit and what was billed. No request names an owner or a PMC; a
   property not theirs reads as not found (`src/pms/api/owner_portal.py`).
3. **Their own contact details.** An owner changes their email, phones and mailing address as a
   tenant already could (q121); staff see on the owner's page that the owner changed them, and
   when.
4. **Messages.** A tenant or an owner writes to the company from their portal, at most 20 a day;
   staff answer from Messages (custom roles' area 'email'), each answer emailed to the person
   too; the Dashboard lists the conversations waiting for an answer. One conversation per
   person, append-only (`trust_messages`): nothing is edited or taken back.
5. **Notice to move out.** A tenant on a lease in effect or to come gives the last day they will
   live there: today or later, within the lease, two years ahead at most, with a note; the latest
   holds (`trust_move_out_notices`). It ends nothing: staff end the lease and return the deposit
   as before. Staff see it on the Dashboard and the lease's page; the owner on the property.
   What notice a lease asks for is the lease's and the state's (`docs/law.md`); the PMS builds in
   no notice period.
6. **Announcements.** Staff post one for the tenants of every property or of one; tenants see
   those of their company and of the properties of their leases in effect or to come, on their
   Overview, until staff take it down (`trust_announcements`, `trust_announcement_removals`). Not
   emailed: Email tenants on a property's page writes to them.
7. **A tenant's documents.** A tenant on a lease in effect sends a document of it (a PDF, JPEG or
   PNG of at most 5 MB, at most 10 a day), such as a renters insurance certificate: a
   `lease_document` that names the tenant who sent it (`trust_documents.uploaded_by_tenant`), on
   the lease's Files for staff.
8. **The AI reads none of it.** Messages, notices, announcements and their removals are in
   `AI_NEVER_READS`, so the audit log hides their rows from `trust_ai_agent` too. None of it moves
   money.

## Consequences

- Migration `20261103000100_portals_two.sql`; its down step refuses while any of it holds data.
- The browser tests for each part are claimed in `ci/features.toml` (owner-portal-two,
  portal-messages, move-out-notice, announcements, tenant-uploads).
- Still out: card payments in the portal (lane 1), texts, and attachments to messages.
