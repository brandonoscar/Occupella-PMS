import { Megaphone } from "lucide-react";
import { useEffect, useState } from "react";
import type { Me } from "../api";
import { ApiError, api } from "../api";
import { Badge } from "../components/Badge";
import { Button } from "../components/Button";
import { EmptyState } from "../components/EmptyState";
import { Field } from "../components/Field";
import { Form } from "../components/Form";
import { Notice } from "../components/Notice";
import { RowMenu } from "../components/RowMenu";
import { Said } from "../components/Said";
import { Card, PageHeader, Shell } from "../components/Shell";
import { usDate } from "../money";
import { STAFF_NAV } from "./staffNav";

export type StaffAnnouncement = {
  id: string;
  property_id: string | null;
  property: string | null;
  title: string;
  body: string;
  at: string;
  by: string;
  removed_at: string | null;
  removed_by: string | null;
};
type PropertyOption = { id: string; name: string };

function words(failed: unknown): string {
  return failed instanceof ApiError ? failed.detail : String(failed);
}

// What tenants see in their portal's Overview (the portals' second version): an announcement for
// one property's tenants or every tenant, up until staff take it down. Not emailed: Email tenants
// on a property's page writes to them.
export function AnnouncementsPage({ me }: { me: Me }) {
  const [found, setFound] = useState<StaffAnnouncement[] | null>(null);
  const [properties, setProperties] = useState<PropertyOption[]>([]);
  const [property, setProperty] = useState("");
  const [title, setTitle] = useState("");
  const [body, setBody] = useState("");
  const [said, setSaid] = useState("");
  const [error, setError] = useState("");

  function refresh() {
    api.get<StaffAnnouncement[]>("/api/announcements").then(setFound).catch((f) => setError(words(f)));
  }
  useEffect(() => {
    refresh();
    api.get<{ items: PropertyOption[] }>("/api/properties?per_page=200&archived=false")
      .then((page) => setProperties(page.items)).catch((f) => setError(words(f)));
  }, []);

  const to = property ? properties.find((p) => p.id === property)?.name ?? "the property" : "every property";
  return (
    <Shell me={me} nav={STAFF_NAV}>
      <PageHeader title="Announcements"
        description="What tenants see on their portal's Overview, for one property or all of them, until you take it down." />
      <Card title="New announcement">
        <Form className="flex flex-col gap-3" onSubmit={async (event) => {
          event.preventDefault();
          setError("");
          setSaid("");
          try {
            await api.post("/api/announcements", { property_id: property || null, title, body });
            setTitle("");
            setBody("");
            setSaid(`Posted for the tenants of ${to}.`);
            refresh();
          } catch (failed) {
            setError(words(failed));
          }
        }}>
          <label htmlFor="announcement-property" className="flex max-w-md flex-col gap-1.5 text-sm text-ink-muted">
            Who sees it
            <select id="announcement-property" value={property} onChange={(e) => setProperty(e.target.value)}
              className="h-9 rounded-sm border border-line-strong bg-sunken px-3 text-base text-ink">
              <option value="">Tenants of every property</option>
              {properties.map((p) => <option key={p.id} value={p.id}>Tenants of {p.name}</option>)}
            </select>
          </label>
          <Field label="Title" id="announcement-title" required maxLength={200} value={title}
            onChange={(e) => setTitle(e.target.value)} />
          <label htmlFor="announcement-body" className="flex flex-col gap-1.5 text-sm text-ink-muted">
            What to say
            <textarea id="announcement-body" required maxLength={4000} rows={4} value={body}
              onChange={(e) => setBody(e.target.value)}
              className="rounded-sm border border-line-strong bg-sunken px-3 py-2 text-base text-ink" />
          </label>
          <Notice tone="error">{error}</Notice>
          <div className="flex items-center gap-4">
            <Button type="submit"
              says={`The tenants of ${to} see it on their portal's Overview right away. It isn't emailed; take it down from its row.`}>
              Post announcement
            </Button>
            <Said>{said}</Said>
          </div>
        </Form>
      </Card>
      <Card title="Posted">
        {found !== null && found.length === 0 && (
          <EmptyState icon={Megaphone} title="No announcements yet." body="Post one above: tenants see it when they sign in." />
        )}
        {found !== null && found.length > 0 && (
          <ul aria-label="Announcements" className="flex flex-col divide-y divide-line">
            {found.map((a) => (
              <li key={a.id} className="flex items-start justify-between gap-4 py-3">
                <div>
                  <p className="flex flex-wrap items-center gap-2 font-medium text-ink">
                    {a.title}
                    {a.removed_at ? <Badge tone="neutral">Taken down</Badge> : <Badge tone="positive">Showing</Badge>}
                  </p>
                  <p className="mt-1 text-base whitespace-pre-wrap text-ink">{a.body}</p>
                  <p className="mt-1 text-sm text-ink-subtle">
                    {a.property ?? "Every property"} · posted by {a.by} on {usDate(a.at)}
                    {a.removed_at && ` · taken down by ${a.removed_by ?? "staff"} on ${usDate(a.removed_at)}`}
                  </p>
                </div>
                {!a.removed_at && (
                  <RowMenu label={a.title} actions={[{
                    label: "Take down",
                    danger: true,
                    confirm: `Take down "${a.title}"? Tenants no longer see it, and it can't be put back up.`,
                    onSelect: async () => {
                      try {
                        await api.post(`/api/announcements/${a.id}/remove`, {});
                        setSaid(`"${a.title}" was taken down.`);
                        refresh();
                      } catch (failed) {
                        setError(words(failed));
                      }
                    },
                  }]} />
                )}
              </li>
            ))}
          </ul>
        )}
      </Card>
    </Shell>
  );
}
