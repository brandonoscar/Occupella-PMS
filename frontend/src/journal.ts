// Journal entries as the API sends them (src/pms/api/journal.py): a correction one staff member
// proposes and another, who holds approve, posts.
import type { BadgeTone } from "./components/Badge";
import { sum } from "./reconcile";

export type JournalAccount = { id: string; kind: string; bank: string; label: string };
export type JournalLine = { from_account: string; to_account: string; amount: string };
export type EntryStatus = "waiting" | "posted" | "declined";
export type JournalEntry = {
  id: string;
  event_on: string;
  memo: string;
  proposed_by: string;
  proposed_by_me: boolean;
  proposed_at: string;
  lines: JournalLine[];
  status: EntryStatus;
  decided_by: string | null;
  decided_at: string | null;
  reason: string | null;
};
export type Journal = { may_post: boolean; accounts: JournalAccount[]; entries: JournalEntry[] };

export function entryTone(status: EntryStatus): BadgeTone {
  return status === "posted" ? "positive" : status === "waiting" ? "caution" : "neutral";
}

// What an entry moves in all, exact to the cent.
export function entryTotal(entry: JournalEntry): string {
  return sum(entry.lines.map((line) => line.amount));
}

export function accountLabel(journal: Journal, id: string): string {
  return journal.accounts.find((a) => a.id === id)?.label ?? id;
}

// Who may post an entry, as the database decides: a staff member who holds approve and did not
// propose it. The page shows the button only to them.
export function mayPost(journal: Journal, entry: JournalEntry): boolean {
  return entry.status === "waiting" && journal.may_post && !entry.proposed_by_me;
}

// Why the signed-in staff member can't post a waiting entry, said where the button would be.
export function whyNotPost(journal: Journal, entry: JournalEntry): string {
  if (entry.status !== "waiting" || mayPost(journal, entry)) return "";
  if (entry.proposed_by_me) return "You proposed it: another staff member who may approve posts it.";
  return "A staff member who may approve posts it.";
}
