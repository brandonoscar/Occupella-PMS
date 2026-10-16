import { useEffect, useState } from "react";
import { api } from "../api";
import type { Scope } from "../scope";

type Ref = { id: string; name: string };
export type Groups = { groups: (Ref & { properties: Ref[] })[]; properties: Ref[] };

// Top left of every list page: All properties, a property group, or one property (groups first),
// as Buildium's selector is. The choice carries across pages (useScope).
export function ScopePicker({ scope, onChange }: { scope: Scope; onChange: (scope: Scope) => void }) {
  const [choices, setChoices] = useState<Groups | null>(null);
  useEffect(() => {
    // Without its choices (a read refused), the picker still offers All properties.
    api
      .get<Groups>("/api/property-groups")
      .then((found) => setChoices({
        groups: Array.isArray(found?.groups) ? found.groups : [],
        properties: Array.isArray(found?.properties) ? found.properties : [],
      }))
      .catch(() => setChoices({ groups: [], properties: [] }));
  }, []);
  const value = scope.group ? `group:${scope.group}` : scope.property ? `property:${scope.property}` : "";
  return (
    <select aria-label="Properties shown" value={value}
      className="h-9 min-w-[200px] rounded-sm border border-line-strong bg-canvas px-2 text-sm font-medium text-ink"
      onChange={(event) => {
        const [kind, id] = event.target.value.split(":");
        onChange(kind === "group" ? { group: id } : kind === "property" ? { property: id } : {});
      }}>
      <option value="">All properties</option>
      {choices && choices.groups.length > 0 && (
        <optgroup label="Property groups">
          {choices.groups.map((group) => (
            <option key={group.id} value={`group:${group.id}`}>{group.name}</option>
          ))}
        </optgroup>
      )}
      {choices && (
        <optgroup label="Properties">
          {choices.properties.map((property) => (
            <option key={property.id} value={`property:${property.id}`}>{property.name}</option>
          ))}
        </optgroup>
      )}
      {/* A choice not offered (an archived property, a link from elsewhere) still shows. */}
      {value && choices && !choices.groups.some((g) => `group:${g.id}` === value)
        && !choices.properties.some((p) => `property:${p.id}` === value) && (
        <option value={value}>{scope.group ? "A property group" : "A property"}</option>
      )}
    </select>
  );
}
