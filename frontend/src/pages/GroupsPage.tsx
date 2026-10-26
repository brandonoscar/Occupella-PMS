import { FolderTree } from "lucide-react";
import { useEffect, useState } from "react";
import type { Me } from "../api";
import { ApiError, api } from "../api";
import { Button } from "../components/Button";
import { EmptyState } from "../components/EmptyState";
import { Field } from "../components/Field";
import { Form } from "../components/Form";
import { Notice } from "../components/Notice";
import { Said } from "../components/Said";
import type { Groups } from "../components/ScopePicker";
import { PageHeader, Shell } from "../components/Shell";
import { SidePanel } from "../components/SidePanel";
import { Wide } from "../components/Wide";
import { STAFF_NAV } from "./staffNav";

type Group = Groups["groups"][number];

function detail(failed: unknown): string {
  return failed instanceof ApiError ? failed.detail : String(failed);
}

// Settings → Property groups (the founder's punch list): named sets of properties ("North
// portfolio") that every list page narrows to. Add group opens a panel; each group is edited in
// one: its name and which properties are in it. A group is never deleted.
export function GroupsPage({ me }: { me: Me }) {
  const [groups, setGroups] = useState<Groups | null>(null);
  const [error, setError] = useState("");
  const [said, setSaid] = useState("");
  const [editing, setEditing] = useState<Group | "new" | null>(null);

  function refresh() {
    api.get<Groups>("/api/property-groups").then(setGroups).catch((failed) => setError(detail(failed)));
  }
  useEffect(refresh, []);

  return (
    <Shell me={me} nav={STAFF_NAV}>
      <PageHeader title="Property groups" actions={<Button onClick={() => setEditing("new")}>Add group</Button>} />
      <Said>{said}</Said>
      {groups === null ? (
        <Notice tone={error ? "error" : "info"}>{error || "Loading"}</Notice>
      ) : groups.groups.length === 0 ? (
        <EmptyState icon={FolderTree} title="No property groups yet."
          body="A group lets every list show just its properties." />
      ) : (
        <Wide>
          <table className="w-full text-base">
            <thead>
              <tr>
                <th className="text-left">Group</th>
                <th className="text-left">Properties</th>
                <th className="text-right">Count</th>
              </tr>
            </thead>
            <tbody>
              {groups.groups.map((group) => (
                <tr key={group.id} aria-label={group.name} className="cursor-pointer hover:bg-raised"
                  onClick={() => setEditing(group)}>
                  <td>
                    <button type="button" className="font-medium text-iris hover:text-iris-hover hover:underline"
                      onClick={(event) => { event.stopPropagation(); setEditing(group); }}>
                      {group.name}
                    </button>
                  </td>
                  <td className="text-ink-muted">{group.properties.map((p) => p.name).join(", ") || "None yet"}</td>
                  <td className="text-right">{group.properties.length}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </Wide>
      )}
      {editing !== null && groups !== null && (
        <GroupPanel group={editing === "new" ? null : editing} offered={groups.properties}
          onClose={() => setEditing(null)}
          onSaved={(done) => { setEditing(null); setSaid(done); refresh(); }} />
      )}
    </Shell>
  );
}

function GroupPanel({
  group,
  offered,
  onClose,
  onSaved,
}: {
  group: Group | null;
  offered: Groups["properties"];
  onClose: () => void;
  onSaved: (said: string) => void;
}) {
  const [name, setName] = useState(group?.name ?? "");
  const [chosen, setChosen] = useState<Set<string>>(new Set(group?.properties.map((p) => p.id) ?? []));
  const [error, setError] = useState("");
  // A group's archived property is still in it until taken out.
  const choices = [...offered, ...(group?.properties.filter((p) => !offered.some((o) => o.id === p.id)) ?? [])];

  async function save() {
    setError("");
    const property_ids = choices.filter((p) => chosen.has(p.id)).map((p) => p.id);
    try {
      if (group === null) {
        await api.post("/api/property-groups", { name, property_ids });
        onSaved(`Added the group ${name.trim()}, with ${property_ids.length} ${property_ids.length === 1 ? "property" : "properties"}.`);
      } else {
        if (name.trim() !== group.name) await api.post(`/api/property-groups/${group.id}/name`, { name });
        await api.post(`/api/property-groups/${group.id}/properties`, { property_ids });
        onSaved(`Saved ${name.trim()}: ${property_ids.length} ${property_ids.length === 1 ? "property" : "properties"}.`);
      }
    } catch (failed) {
      setError(detail(failed));
    }
  }

  return (
    <SidePanel title={group === null ? "Add group" : `Edit ${group.name}`} onClose={onClose}>
      <Form className="flex flex-col gap-4" onSubmit={(event) => { event.preventDefault(); return save(); }}>
        <Field label="Name" id="group-name" required maxLength={100} value={name}
          onChange={(event) => setName(event.target.value)} />
        <fieldset className="flex flex-col gap-2">
          <legend className="mb-1 text-sm text-ink-muted">Properties</legend>
          {choices.length === 0 && <p className="text-sm text-ink-subtle">No properties yet.</p>}
          {choices.map((property) => (
            <label key={property.id} className="flex items-center gap-2 text-base text-ink">
              <input type="checkbox" checked={chosen.has(property.id)}
                onChange={(event) => {
                  const next = new Set(chosen);
                  if (event.target.checked) next.add(property.id);
                  else next.delete(property.id);
                  setChosen(next);
                }} />
              {property.name}
            </label>
          ))}
        </fieldset>
        <Notice tone="error">{error}</Notice>
        <div className="flex gap-2">
          <Button type="submit" says={group === null
            ? "Every list can then show just these properties. Nothing about the properties changes."
            : "Saves its name and which properties are in it. Nothing about the properties changes."}>
            {group === null ? "Add group" : "Save"}
          </Button>
          <Button type="button" variant="secondary" onClick={onClose}>Cancel</Button>
        </div>
      </Form>
    </SidePanel>
  );
}
