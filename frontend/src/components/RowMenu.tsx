import { MoreHorizontal } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { ConfirmDialog } from "./Confirm";

export type RowAction = {
  label: string;
  onSelect: () => unknown;
  // Asked first, naming what it does and any amount ("Mark the $1,395.00 payment bounced?").
  confirm?: string;
  danger?: boolean;
};

// A row's risky actions behind ⋯ (the founder's punch list: It bounced, Archive, Stop, Undo,
// Decline, Disable, Void), each one confirmed before it is sent.
export function RowMenu({ label, actions }: { label: string; actions: RowAction[] }) {
  const [open, setOpen] = useState(false);
  const [asking, setAsking] = useState<RowAction | null>(null);
  const box = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const close = (event: Event) => {
      if (event instanceof KeyboardEvent ? event.key === "Escape" : !box.current?.contains(event.target as Node)) {
        setOpen(false);
      }
    };
    window.addEventListener("keydown", close);
    window.addEventListener("mousedown", close);
    return () => {
      window.removeEventListener("keydown", close);
      window.removeEventListener("mousedown", close);
    };
  }, [open]);

  function choose(action: RowAction) {
    setOpen(false);
    if (action.confirm) setAsking(action);
    else void action.onSelect();
  }

  return (
    <div ref={box} className="relative inline-block">
      <button type="button" aria-label={`Actions for ${label}`} aria-haspopup="menu" aria-expanded={open}
        onClick={() => setOpen((was) => !was)}
        className="inline-flex h-8 w-8 items-center justify-center rounded-sm text-ink-muted hover:bg-raised hover:text-ink">
        <MoreHorizontal size={16} strokeWidth={1.75} aria-hidden="true" />
      </button>
      {open && (
        <div role="menu" aria-label={`Actions for ${label}`}
          className="absolute right-0 z-30 mt-1 flex min-w-[180px] flex-col rounded-md border border-card-edge bg-canvas py-1 shadow-lg">
          {actions.map((action) => (
            <button key={action.label} type="button" role="menuitem" onClick={() => choose(action)}
              className={"px-3 py-2 text-left text-sm hover:bg-raised " + (action.danger ? "text-danger" : "text-ink")}>
              {action.label}
            </button>
          ))}
        </div>
      )}
      {asking && (
        <ConfirmDialog question={asking.confirm ?? asking.label} confirmLabel={asking.label}
          danger={asking.danger}
          onConfirm={() => {
            const act = asking;
            setAsking(null);
            void act.onSelect();
          }}
          onCancel={() => setAsking(null)} />
      )}
    </div>
  );
}
