import { Scale, TriangleAlert } from "lucide-react";
import type { Checked } from "../law";
import { citedLine, confirmedLine } from "../law";

const LINK = "font-medium text-iris hover:text-iris-hover hover:underline";

// The rules a field must keep to, beside it (q142, src/pms/law.ts): each rule cited with the day
// it was read and whether the company confirmed it, or why it is unknown; then what goes past a
// known rule, in the caution colour on white with its icon (the caution colour fails contrast on
// its own tint), as a warning: the form saves either way. Nothing is shown before the first answer.
export function RulesBeside({ checked, label, className = "" }: {
  checked: Checked | null; label: string; className?: string;
}) {
  if (checked === null) return null;
  return (
    <section aria-label={label} className={`flex flex-col gap-2 text-sm ${className}`}>
      <ul className="flex flex-col gap-1.5">
        {checked.rules.map((r) => (
          <li key={r.rule} className="flex items-start gap-1.5 text-ink-muted">
            <Scale size={14} strokeWidth={1.9} aria-hidden="true" className="mt-0.5 shrink-0" />
            <span>
              <span className="font-medium text-ink">{`${r.label}: `}</span>
              {r.words}
              {r.known && (
                <>
                  {". "}
                  {r.url ? (
                    <a className={LINK} href={r.url} target="_blank" rel="noopener noreferrer">{citedLine(r)}</a>
                  ) : citedLine(r)}
                  {`. ${confirmedLine(r)}.`}
                  {r.exceptions && ` Exceptions: ${r.exceptions}`}
                  {r.here && ` ${r.here}`}
                </>
              )}
            </span>
          </li>
        ))}
      </ul>
      {checked.warnings.length > 0 && (
        <div role="status" className="flex flex-col gap-1 rounded-md border border-caution bg-canvas px-3 py-2">
          <p className="flex items-center gap-1.5 font-medium text-caution">
            <TriangleAlert size={14} strokeWidth={1.9} aria-hidden="true" className="shrink-0" />
            Past a rule: a warning, not a refusal
          </p>
          <ul className="flex list-disc flex-col gap-1 pl-5 text-ink">
            {checked.warnings.map((w) => <li key={`${w.rule}:${w.said}`}>{w.said}</li>)}
          </ul>
        </div>
      )}
      <p className="text-xs text-ink-muted">{checked.note}</p>
    </section>
  );
}
