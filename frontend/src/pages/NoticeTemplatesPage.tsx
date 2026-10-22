import { FileText } from "lucide-react";
import { useEffect, useState } from "react";
import type { Me } from "../api";
import { ApiError, api } from "../api";
import { Button } from "../components/Button";
import { EmptyState } from "../components/EmptyState";
import { Field } from "../components/Field";
import { Form } from "../components/Form";
import { Notice } from "../components/Notice";
import { RowMenu } from "../components/RowMenu";
import { Said } from "../components/Said";
import { PageHeader, Shell } from "../components/Shell";
import { SidePanel } from "../components/SidePanel";
import { Wide } from "../components/Wide";
import { usDate } from "../money";
import type { Blank, NoticeTemplate, Templates } from "../notices";
import { blank } from "../notices";
import { STAFF_NAV } from "./staffNav";

function detail(failed: unknown): string {
  return failed instanceof ApiError ? failed.detail : String(failed);
}

// Settings → Notice templates (the founder's review q095): the company's own notices (late rent,
// entry, non-renewal, anything it writes), with blanks the PMS fills from a lease. A lease's page
// makes one into a notice for it. The words are the company's: the PMS writes no notice text and
// checks no state's notice period or wording.
export function NoticeTemplatesPage({ me }: { me: Me }) {
  const [found, setFound] = useState<Templates | null>(null);
  const [error, setError] = useState("");
  const [said, setSaid] = useState("");
  const [editing, setEditing] = useState<NoticeTemplate | "new" | null>(null);

  function refresh() {
    api.get<Templates>("/api/notices/templates").then(setFound).catch((failed) => setError(detail(failed)));
  }
  useEffect(refresh, []);

  async function remove(template: NoticeTemplate) {
    setError("");
    try {
      await api.post(`/api/notices/templates/${template.id}/remove`, {});
      setSaid(`${template.name} is removed; the notices made from it stay.`);
      refresh();
    } catch (failed) {
      setError(detail(failed));
    }
  }

  return (
    <Shell me={me} nav={STAFF_NAV}>
      <PageHeader title="Notice templates" actions={<Button onClick={() => setEditing("new")}>Add template</Button>} />
      <p className="text-sm text-ink-muted">
        Your own notices, in your own words. The PMS fills in the blanks from a lease and checks no state's notice
        period or wording; have yours reviewed for the states you work in.
      </p>
      <Said>{said}</Said>
      {editing === null && <Notice tone="error">{error}</Notice>}
      {found === null ? (
        !error && <Notice tone="info">Loading</Notice>
      ) : found.templates.length === 0 ? (
        <EmptyState icon={FileText} title="No notice template yet."
          body="Add one, and a lease's Files tab makes it into a notice for that lease." />
      ) : (
        <Wide>
          <table className="w-full text-base" aria-label="Notice templates">
            <thead>
              <tr>
                <th className="text-left">Template</th>
                <th className="text-left">Saved</th>
                <th><span className="sr-only">Actions</span></th>
              </tr>
            </thead>
            <tbody>
              {found.templates.map((template) => (
                <tr key={template.id} aria-label={template.name} className="cursor-pointer hover:bg-raised"
                  onClick={() => setEditing(template)}>
                  <td className="font-medium text-ink">{template.name}</td>
                  <td className="text-sm text-ink-muted">
                    {usDate(template.saved_at.slice(0, 10))}{template.saved_by ? ` by ${template.saved_by}` : ""}
                  </td>
                  <td className="text-right" onClick={(event) => event.stopPropagation()}>
                    <RowMenu label={template.name} actions={[
                      { label: "Remove", danger: true, onSelect: () => remove(template),
                        confirm: `Remove ${template.name}? The notices made from it stay.` },
                    ]} />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </Wide>
      )}
      {editing !== null && found !== null && (
        <SidePanel title={editing === "new" ? "Add a notice template" : `Edit ${editing.name}`} onClose={() => setEditing(null)}>
          <TemplateForm template={editing === "new" ? null : editing} fields={found.fields}
            onSaved={(saved) => {
              setSaid(`${saved.name} is saved.`);
              setEditing(null);
              refresh();
            }} />
        </SidePanel>
      )}
    </Shell>
  );
}

function TemplateForm({ template, fields, onSaved }: {
  template: NoticeTemplate | null;
  fields: Blank[];
  onSaved: (saved: NoticeTemplate) => void;
}) {
  const [name, setName] = useState(template?.name ?? "");
  const [body, setBody] = useState(template?.body ?? "");
  const [error, setError] = useState("");
  const path = template === null ? "/api/notices/templates" : `/api/notices/templates/${template.id}`;

  return (
    <Form className="flex flex-col gap-3" onSubmit={async (event) => {
      event.preventDefault();
      setError("");
      try {
        onSaved(await api.post<NoticeTemplate>(path, { name, body }));
      } catch (failed) {
        setError(detail(failed));
      }
    }}>
      <Field label="Name" id="notice-name" required maxLength={100} value={name}
        onChange={(event) => setName(event.target.value)} />
      <label htmlFor="notice-body" className="text-sm text-ink-muted">Text</label>
      <textarea id="notice-body" required maxLength={20000} rows={12} value={body}
        onChange={(event) => setBody(event.target.value)}
        className="rounded-sm border border-line-strong bg-sunken px-2 py-1.5 text-base text-ink" />
      <Notice tone="error">{error}</Notice>
      <div><Button type="submit">Save template</Button></div>
      <section aria-label="Blanks the PMS fills" className="flex flex-col gap-1 text-sm">
        <p className="text-ink-muted">Blanks the PMS fills from the lease:</p>
        <ul className="flex flex-col gap-1">
          {fields.map((field) => (
            <li key={field.name}>
              <code className="break-all font-mono text-ink">{blank(field.name)}</code>{" "}
              <span className="text-ink-muted">{field.what}</span>
            </li>
          ))}
        </ul>
      </section>
    </Form>
  );
}
