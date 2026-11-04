// ADR 0010, rule 1, reversed by the founder's review of 11/03 (item 5): a button's label says what
// it does, and nothing more is written under it, like Stripe's or Linear's. A plain line under a
// button (`says`, components/Button.tsx) stays only where it stops a mistake: money leaving trust
// or moving between people, someone emailed or shown something, a step that can't be taken back,
// or one that does more (or less) than its label suggests. Each such button is listed below with
// why; a new line fails until it is listed, and a listed button that lost its line fails too.
// This reads every <Button> in the portal's source, its attributes scanned to the tag's own `>`
// (braces and quotes skipped).
import { readdirSync, readFileSync, statSync } from "node:fs";
import { join, relative } from "node:path";
import { describe, expect, it } from "vitest";

const SRC = join(process.cwd(), "src");

function sources(dir: string): string[] {
  return readdirSync(dir).flatMap((name) => {
    const path = join(dir, name);
    if (statSync(path).isDirectory()) return sources(path);
    return name.endsWith(".tsx") && !name.endsWith(".test.tsx") ? [path] : [];
  });
}

type Tag = { file: string; line: number; attributes: string; label: string };

// Each <Button ...>label</Button> in a file: its attributes as written and its label's text.
function buttons(file: string, text: string): Tag[] {
  const found: Tag[] = [];
  for (const match of text.matchAll(/<Button\b/g)) {
    let at = match.index + "<Button".length;
    let depth = 0;
    let quote: string | null = null;
    for (; at < text.length; at++) {
      const c = text[at];
      if (quote) {
        if (c === quote && text[at - 1] !== "\\") quote = null;
      } else if (c === '"' || c === "'" || c === "`") quote = c;
      else if (c === "{") depth++;
      else if (c === "}") depth--;
      else if (c === ">" && depth === 0) break;
    }
    const attributes = text.slice(match.index + "<Button".length, at);
    const selfClosing = attributes.trimEnd().endsWith("/");
    const end = selfClosing ? at : text.indexOf("</Button>", at);
    const label = selfClosing ? "" : text.slice(at + 1, end).replace(/\s+/g, " ").trim();
    const line = text.slice(0, match.index).split("\n").length;
    found.push({ file, line, attributes, label });
  }
  return found;
}

type Why =
  | "money"          // money leaves trust, or moves from an owner or tenant to someone else
  | "sends"          // someone outside the company is emailed or sent something
  | "who sees"       // a file or a link someone outside the company then sees
  | "for good"       // it can't be taken back
  | "shown once"     // a link shown once, to copy now
  | "signing"        // a legal step: e-sign consent, a signature, an adverse action notice
  | "more than said" // it does more, or less, than its label suggests
  | "left as it was"; // renters insurance, which the founder said stays as it is

// The buttons that keep their line: the file, the label as written (its start), and why.
const KEEP: [string, string, Why][] = [
  ["components/EmailsCard.tsx", "Send email", "sends"],
  ["components/MessagesCard.tsx", "Send message", "sends"],
  ["pages/ApplicantPage.tsx", '{rejecting ? "Reject" : "Approve"}', "signing"],
  ["pages/ApplicantPage.tsx", "Record withdrawal", "for good"],
  ["pages/ApplyPage.tsx", "Send application", "for good"],
  ["pages/AttentionActions.tsx", '{n === 0 ? "No one to send it to"', "sends"],
  ["pages/AttentionActions.tsx", "Pay {money(total)}", "money"],
  ["pages/AutomaticFeesCard.tsx", "Save", "money"],
  ["pages/AutopayCards.tsx", "Save a bank account and turn on autopay", "more than said"],
  ["pages/BillForms.tsx", "Record decline", "for good"],
  ["pages/BillForms.tsx", 'Pay {money(valid ? amount : "0")}', "money"],
  ["pages/BillForms.tsx", "Void", "for good"],
  ["pages/BillsPage.tsx", "Stop it", "for good"],
  ["pages/CalendarCard.tsx", '{standing ? "Make a new link"', "shown once"],
  ["pages/ChartOfAccountsPage.tsx", "Add account", "for good"],
  ["pages/CompanyFeesForm.tsx", "Take out company fees", "money"],
  ["pages/DepositInterestForm.tsx", "Pay interest", "money"],
  ["pages/DocumentsPage.tsx", "Upload", "who sees"],
  ["pages/DraftLeaseForm.tsx", "Send for signing", "sends"],
  ["pages/DraftLeasePage.tsx", "Send", "sends"],
  ["pages/DraftLeasePage.tsx", "Activate lease", "more than said"],
  ["pages/DraftLeasePage.tsx", "Void it", "for good"],
  ["pages/EmailTenantsPanel.tsx", '{n === 0 ? "Choose who gets it"', "sends"],
  ["pages/FilesCard.tsx", "Upload", "who sees"],
  ["pages/InsuranceForms.tsx", "Set insurance terms", "left as it was"],
  ["pages/InsuranceForms.tsx", "Record policy", "left as it was"],
  ["pages/JournalPage.tsx", "Decline entry", "for good"],
  ["pages/LeadPage.tsx", "Close lead", "for good"],
  ["pages/LeaseForms.tsx", "Record the {money(payment.amount)} bounce", "more than said"],
  ["pages/LeaseForms.tsx", "Record payment", "sends"],
  ["pages/LeaseForms.tsx", "Place hold", "more than said"],
  ["pages/LeaseForms.tsx", "Waive {money(amount)}", "for good"],
  ["pages/LeasePage.tsx", '{person.portal === "no login" ? "Invite"', "sends"],
  ["pages/LeasePeople.tsx", "Take {person.name} off the lease", "money"],
  ["pages/LeasePeople.tsx", "End lease", "more than said"],
  ["pages/LeasingFeeForm.tsx", "Take leasing fee", "money"],
  ["pages/MoveOutPage.tsx", "Email the statement", "sends"],
  ["pages/MoveOutPage.tsx", "Return the deposit", "money"],
  ["pages/OwnerPayCard.tsx", "Pay by bank", "more than said"],
  ["pages/OwnersPage.tsx", "Take fee", "money"],
  ["pages/PayBillsPage.tsx", '{picked.length === 0 ? "Choose bills to pay"', "money"],
  ["pages/PayOnlineCard.tsx", "Pay online", "more than said"],
  ["pages/PayOwnersPage.tsx", '{payouts.length === 0 ? "Choose owners to pay"', "money"],
  ["pages/PaymentFilesPages.tsx", "Bring it in", "sends"],
  ["pages/PaymentFilesPages.tsx", "Record {money(line.amount)} to this lease", "sends"],
  ["pages/PaymentFilesPages.tsx", "Skip this line", "for good"],
  ["pages/PortalCard.tsx", '{portal !== "no login" ? "Send a new invite"', "sends"],
  ["pages/PropertyUtilitiesPage.tsx", '{preview ? "Charge the leases"', "more than said"],
  ["pages/ReconcilePage.tsx", "Add bank account", "for good"],
  ["pages/RenewLeaseForm.tsx", "Send for signing", "sends"],
  ["pages/RentPage.tsx", "Run rent now", "more than said"],
  ["pages/RentalListingsPage.tsx", "Open the link", "shown once"],
  ["pages/SignPage.tsx", "Continue", "signing"],
  ["pages/SignPage.tsx", "Open the lease (PDF)", "signing"],
  ["pages/SignPage.tsx", "Sign the lease", "signing"],
  ["pages/SignersCard.tsx", "Record", "more than said"],
  ["pages/SwitchPage.tsx", "Import", "sends"],
  ["pages/SwitchPage.tsx", "Open with balances", "for good"],
  ["pages/TaxIdCard.tsx", "Reveal", "more than said"],
  ["pages/TenantPortalCards.tsx", "Give notice", "more than said"],
  ["pages/TenantPortalCards.tsx", "Send document", "who sees"],
  ["pages/VendorLinkPage.tsx", "The work is done", "for good"],
  ["pages/WorkOrdersPages.tsx", "Stop it", "for good"],
  ["pages/WorkOrdersPages.tsx", "Charge", "more than said"],
  ["pages/WorkOrdersPages.tsx", "Record", "sends"],
];

const kept = (tag: Tag) =>
  KEEP.some(([file, label]) => tag.file === file && (tag.label === label || (label.length > 12 && tag.label.startsWith(label))));
const ALL = sources(SRC).flatMap((path) => buttons(relative(SRC, path), readFileSync(path, "utf8")));
const SAYING = ALL.filter((tag) => /\bsays=/.test(tag.attributes));

describe("rule 1: a button says what it does; a line under it only where it stops a mistake", () => {
  it("finds the portal's buttons", () => {
    expect(ALL.filter((tag) => /type="submit"/.test(tag.attributes)).length).toBeGreaterThan(100);
  });

  it("a button keeps a line under it only where it is listed with why", () => {
    const unlisted = SAYING.filter((tag) => !kept(tag)).map((tag) => `${tag.file}:${tag.line} ${tag.label}`);
    expect(unlisted).toEqual([]);
  });

  it("every listed button still has its line", () => {
    const lost = KEEP.filter(([file, label]) => !SAYING.some((tag) => kept(tag) && tag.file === file
      && (tag.label === label || tag.label.startsWith(label))))
      .map(([file, label]) => `${file} ${label}`);
    expect(lost).toEqual([]);
  });

  it("reads a tag's attributes past arrows and strings", () => {
    const [tag] = buttons("x.tsx", `<Button type="submit" onClick={() => go(">")} says={\`a > b\`}>Pay {money(x)}</Button>`);
    expect(tag?.attributes).toContain("says=");
    expect(tag?.label).toBe("Pay {money(x)}");
  });
});
