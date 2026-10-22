// The company's notice templates and the notices made from them (src/pms/api/notice_templates.py,
// the founder's review q095).
export type NoticeTemplate = {
  id: string;
  version_id: string;
  name: string;
  body: string;
  saved_at: string;
  saved_by: string | null;
};

export type Blank = { name: string; what: string };

export type Templates = { templates: NoticeTemplate[]; fields: Blank[] };

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
