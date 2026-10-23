// Announcements as the API sends them (src/pms/api/announcements.py, migration
// 20261023000203): what staff wrote once to every tenant or owner of a place.
export type Audience = "tenants" | "owners";

export type Recipient = { id: string; name: string; to: string };

export type Recipients = {
  emailed: Recipient[];
  without_email: string[];
  // "every tenant living at Cedar House"
  words: string;
};

export type Sent = Recipients & { id: string };

export type AnnouncementLine = {
  id: string;
  subject: string;
  words: string;
  by: string;
  at: string;
  emails: number;
  sent: number;
  failed: number;
};

export type Announcement = {
  id: string;
  subject: string;
  body: string;
  words: string;
  by: string;
  at: string;
  emails: { name: string; to: string; status: "sent" | "waiting" | "failed"; error: string | null }[];
};

// Who an announcement would go to, in words: "Goes to 2 tenants. No email on file: Tenant B."
export function goesTo(found: Recipients, audience: Audience): string {
  const people = audience === "tenants" ? "tenant" : "owner";
  const n = found.emailed.length;
  const head = `Goes to ${n} ${people}${n === 1 ? "" : "s"}, ${found.words}.`;
  return found.without_email.length > 0 ? `${head} No email on file: ${found.without_email.join(", ")}.` : head;
}

// What sending did: "Emailed 2 tenants. No email on file: Tenant B."
export function sentLine(sent: Sent, audience: Audience): string {
  const people = audience === "tenants" ? "tenant" : "owner";
  const n = sent.emailed.length;
  const head = `Emailed ${n} ${people}${n === 1 ? "" : "s"}.`;
  return sent.without_email.length > 0 ? `${head} No email on file: ${sent.without_email.join(", ")}.` : head;
}

// How many of an announcement's emails went: "2 of 3 sent, 1 failed".
export function wentLine(line: AnnouncementLine): string {
  const failed = line.failed > 0 ? `, ${line.failed} failed` : "";
  return `${line.sent} of ${line.emails} sent${failed}`;
}
