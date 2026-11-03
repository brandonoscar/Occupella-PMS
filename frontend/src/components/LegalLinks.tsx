// The company's own terms of use and privacy notice, where it keeps them (Company setup;
// migration 0210): the company answers to its tenants, owners and applicants for what it keeps
// of them, so the words are its own, never the PMS's. Nothing shows when it set neither.
const LINK = "font-medium text-iris hover:text-iris-hover hover:underline";

export type Legal = { terms_url: string | null; privacy_url: string | null };

export function LegalLinks({ company, legal }: { company: string; legal: Legal }) {
  const links = [
    legal.terms_url && { href: legal.terms_url, label: "terms of use" },
    legal.privacy_url && { href: legal.privacy_url, label: "privacy notice" },
  ].filter((link): link is { href: string; label: string } => Boolean(link));
  if (links.length === 0) return null;
  return (
    <p className="text-sm text-ink-muted">
      {company}&apos;s{" "}
      {links.map((link, n) => (
        <span key={link.label}>
          {n > 0 && " and "}
          <a className={LINK} href={link.href} target="_blank" rel="noopener noreferrer">{link.label}</a>
        </span>
      ))}
    </p>
  );
}
