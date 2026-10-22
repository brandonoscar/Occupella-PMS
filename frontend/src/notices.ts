// The company's notice templates and the notices made from them (src/pms/api/notice_templates.py,
// the founder's review q095).
// What a template is for, from when it is made (migration 0121): a notice on a lease, or a lease
// to sign, filled from a draft lease's terms.
export type TemplateUse = "notice" | "lease";
export const USE_LABEL: Record<TemplateUse, string> = { notice: "A notice on a lease", lease: "A lease to sign" };

export type NoticeTemplate = {
  id: string;
  version_id: string;
  name: string;
  body: string;
  used_for: TemplateUse;
  saved_at: string;
  saved_by: string | null;
};

export type Blank = { name: string; what: string };

// fields: a notice template's blanks; lease_fields: a lease template's.
export type Templates = { templates: NoticeTemplate[]; fields: Blank[]; lease_fields: Blank[] };

export type Filled = { title: string; letterhead: string; body: string };

export type MadeNotice = {
  id: string;
  lease_id: string;
  title: string;
  dated_on: string;
  made_at: string;
  made_by: string | null;
};

// "{{tenant_names}}", as a template writes a blank.
export function blank(name: string): string {
  return `{{${name}}}`;
}
