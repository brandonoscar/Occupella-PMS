import { describe, expect, it } from "vitest";
import type { Journal, JournalEntry } from "./journal";
import { accountLabel, entryTone, entryTotal, mayPost, whyNotPost } from "./journal";

const entry = (over: Partial<JournalEntry> = {}): JournalEntry => ({
  id: "e1",
  event_on: "2026-02-10",
  memo: "Rent deposited to the wrong property",
  proposed_by: "Bookkeeper 1",
  proposed_by_me: false,
  proposed_at: "2026-02-10T15:00:00Z",
  lines: [
    { from_account: "pgla_1", to_account: "pgla_2", amount: "0.10" },
    { from_account: "pgla_2", to_account: "pgla_3", amount: "0.20" },
  ],
  status: "waiting",
  decided_by: null,
  decided_at: null,
  reason: null,
  ...over,
});
const journal = (may_post: boolean): Journal => ({
  may_post,
  accounts: [{ id: "pgla_1", kind: "owner_property", bank: "Operating", label: "Operating: Owner 1 / Property 1" }],
  entries: [],
});

describe("journal entries", () => {
  it("adds an entry's lines to the cent, without a float", () => {
    expect(entryTotal(entry())).toBe("0.30");
  });

  it("names an account in words, or by its id when it is not listed", () => {
    expect(accountLabel(journal(true), "pgla_1")).toBe("Operating: Owner 1 / Property 1");
    expect(accountLabel(journal(true), "pgla_9")).toBe("pgla_9");
  });

  it("shows each state as a badge tone", () => {
    expect([entryTone("waiting"), entryTone("posted"), entryTone("declined")]).toEqual([
      "caution",
      "positive",
      "neutral",
    ]);
  });

  it("lets only another staff member who may approve post a waiting entry", () => {
    expect(mayPost(journal(true), entry())).toBe(true);
    expect(mayPost(journal(true), entry({ proposed_by_me: true }))).toBe(false);
    expect(mayPost(journal(false), entry())).toBe(false);
    expect(mayPost(journal(true), entry({ status: "posted" }))).toBe(false);
    expect(whyNotPost(journal(true), entry({ proposed_by_me: true }))).toBe(
      "You proposed it: another staff member who may approve posts it.",
    );
    expect(whyNotPost(journal(false), entry())).toBe("A staff member who may approve posts it.");
    expect(whyNotPost(journal(true), entry())).toBe("");
    expect(whyNotPost(journal(false), entry({ status: "declined" }))).toBe("");
  });
});
