import type { LucideIcon } from "lucide-react";

// What a page shows when it has nothing to list, as Occupella's EmptyState: an icon in a
// hairline circle, one calm sentence and, if it helps, a line on what comes next.
export function EmptyState({
  icon: Icon,
  title,
  body,
}: {
  icon: LucideIcon;
  title: string;
  body?: React.ReactNode;
}) {
  return (
    <div
      data-testid="empty-state"
      className="flex flex-col items-center justify-center gap-3 rounded-md border border-card-edge px-6 py-10 text-center"
    >
      <span className="flex h-12 w-12 items-center justify-center rounded-full border border-line bg-elevated text-ink-muted">
        <Icon size={20} strokeWidth={1.75} aria-hidden="true" />
      </span>
      <p className="text-md font-medium text-ink">{title}</p>
      {body && <p className="max-w-[420px] text-base leading-relaxed text-ink-muted">{body}</p>}
    </div>
  );
}
