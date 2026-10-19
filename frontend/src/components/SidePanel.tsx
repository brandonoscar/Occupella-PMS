import { X } from "lucide-react";
import { useEffect } from "react";

// A form that opens beside the page (ADR 0005: "Record bill opens a side panel"): a dialog at
// the right over a scrim, its title at the top and Close; Escape or the scrim closes it too.
export function SidePanel({
  title,
  onClose,
  children,
}: {
  title: string;
  onClose: () => void;
  children: React.ReactNode;
}) {
  useEffect(() => {
    const close = (event: KeyboardEvent) => event.key === "Escape" && onClose();
    window.addEventListener("keydown", close);
    return () => window.removeEventListener("keydown", close);
  }, [onClose]);
  return (
    <>
      {/* Clicking beside the panel closes it, as Escape and its Close button do. */}
      <div data-testid="panel-scrim" role="presentation" className="fixed inset-0 z-40 bg-scrim" onClick={onClose} />
      <div
        role="dialog"
        aria-modal="true"
        aria-label={title}
        className="fixed inset-y-0 right-0 z-50 flex w-full max-w-md flex-col gap-4 overflow-y-auto border-l border-card-edge bg-canvas p-6 shadow-lg"
      >
        <div className="flex items-center justify-between gap-3">
          <h2 className="font-display text-xl text-ink">{title}</h2>
          <button type="button" aria-label="Close" onClick={onClose}
            className="rounded-sm p-1 text-ink-muted hover:bg-raised hover:text-ink">
            <X size={16} strokeWidth={1.75} aria-hidden="true" />
          </button>
        </div>
        {children}
      </div>
    </>
  );
}
