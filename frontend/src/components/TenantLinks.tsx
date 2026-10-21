import { GoLink } from "../pages/NeedsAttention";

type Ref = { id: string; name: string };

// A lease's tenants, each name opening their own page (not the lease's).
export function TenantLinks({ people }: { people: Ref[] }) {
  return (
    <>
      {people.map((person, n) => (
        <span key={person.id}>
          {n > 0 && ", "}
          <GoLink href={`/tenants/${person.id}`} className="text-iris hover:text-iris-hover hover:underline">
            {person.name}
          </GoLink>
        </span>
      ))}
    </>
  );
}
