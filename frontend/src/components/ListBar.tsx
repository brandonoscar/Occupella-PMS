import type { Scope } from "../scope";
import { ScopePicker } from "./ScopePicker";

// The top left of a list page: which properties it shows, and for records that are archived,
// Active or Archived (the founder's punch list: one picker on every list page, no page search).
export function ListBar({
  scope,
  onScope,
  archived,
  onArchived,
  children,
}: {
  scope: Scope;
  onScope: (scope: Scope) => void;
  archived?: boolean;
  onArchived?: (archived: boolean) => void;
  children?: React.ReactNode;
}) {
  return (
    <div className="flex flex-wrap items-center gap-3">
      <ScopePicker scope={scope} onChange={onScope} />
      {onArchived && (
        <select aria-label="Status" value={archived ? "archived" : "active"}
          className="h-9 rounded-sm border border-line-strong bg-canvas px-2 text-sm text-ink"
          onChange={(event) => onArchived(event.target.value === "archived")}>
          <option value="active">Active</option>
          <option value="archived">Archived</option>
        </select>
      )}
      {children}
    </div>
  );
}
