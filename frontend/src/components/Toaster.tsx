import { AlertCircle, CheckCircle2, X } from "lucide-react";
import { dismiss, useToasts } from "../toast";

// The toasts at the bottom right of every page: a save's message in green, a refusal in the
// danger colour, each with its icon (colour is never the only sign) and a way to close it. The
// page's own status line says the same to a screen reader and holds the text, so a toast is only
// drawn: hidden from assistive technology, its words shown from an attribute.
export function Toaster() {
  const toasts = useToasts();
  if (toasts.length === 0) return null;
  return (
    <div aria-hidden="true" data-testid="toaster" className="fixed right-4 bottom-4 z-50 flex w-[360px] max-w-[calc(100vw-2rem)] flex-col gap-2">
      {toasts.map((toast) => {
        const Icon = toast.tone === "success" ? CheckCircle2 : AlertCircle;
        return (
          <div key={toast.id}
            className={
              "flex items-start gap-2 rounded-md border px-3 py-2.5 text-sm shadow-lg " +
              (toast.tone === "success"
                ? "border-positive bg-positive-bg text-positive"
                : "border-danger bg-danger-bg text-danger")
            }>
            <Icon size={16} strokeWidth={1.9} className="mt-0.5 shrink-0" />
            {/* Drawn from the attribute, so the page's text is in the DOM once: its own status line. */}
            <span data-text={toast.text} className="flex-1 text-ink before:content-[attr(data-text)] first-letter:uppercase" />
            <button type="button" tabIndex={-1} onClick={() => dismiss(toast.id)} className="rounded-sm p-0.5 text-ink-muted hover:text-ink">
              <X size={14} strokeWidth={1.75} />
            </button>
          </div>
        );
      })}
    </div>
  );
}
