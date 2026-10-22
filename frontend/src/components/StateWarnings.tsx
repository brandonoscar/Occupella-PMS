import { TriangleAlert } from "lucide-react";
import { GoLink } from "../pages/NeedsAttention";
import type { Checked } from "../stateRules";

// What a state rules check found (q142): each warning in words, in the caution colour on white
// with its icon (the caution colour fails contrast on its own tint), then the table's note that no
// attorney has reviewed it and a link to the table. Nothing is shown when nothing was found; a
// property with no address says why nothing was checked.
export function StateWarnings({ checked, label, className = "" }: {
  checked: Checked | null; label: string; className?: string;
}) {
  if (checked === null) return null;
  if (checked.state === null) {
    return (
      <p className={`text-sm text-ink-muted ${className}`}>
        No address on file for this property, so no state&apos;s rules were checked.
      </p>
    );
  }
  if (checked.warnings.length === 0) return null;
  return (
    <section aria-label={label} className={`flex flex-col gap-2 rounded-md border border-caution bg-canvas px-3 py-2 text-sm ${className}`}>
      <p className="flex items-center gap-1.5 font-medium text-caution">
        <TriangleAlert size={14} strokeWidth={1.9} aria-hidden="true" className="shrink-0" />
        {checked.state} rules: a warning, not a refusal
      </p>
      <ul className="flex list-disc flex-col gap-1 pl-5 text-ink">
        {checked.warnings.map((w) => (
          <li key={`${w.rule}:${w.said}`}>
            {w.said}
            {w.how === "secondary" ? " (Not verified: a summary only.)" : ""}
          </li>
        ))}
      </ul>
      <p className="text-xs text-ink-muted">
        {checked.note}{" "}
        <GoLink href="/settings/state-rules" className="text-iris underline hover:text-iris-hover">The state rules</GoLink>
      </p>
    </section>
  );
}
