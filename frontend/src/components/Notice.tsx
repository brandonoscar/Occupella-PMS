import { AlertCircle } from "lucide-react";

// A message about what just happened. An error is announced, in the danger colour with an icon
// beside it (colour is never the only sign), as Occupella's FormError; anything else is muted.
// The API's messages start in lower case; the first letter is shown as a capital, and the text
// itself stays the API's.
export function Notice({ tone, children }: { tone: "error" | "info"; children: React.ReactNode }) {
  if (!children) return null;
  if (tone === "info") {
    return (
      <p role="status" className="text-sm text-ink-muted first-letter:uppercase">
        {children}
      </p>
    );
  }
  return (
    <p role="alert" className="flex items-start gap-1.5 text-sm text-danger">
      <AlertCircle size={14} strokeWidth={1.9} aria-hidden="true" className="mt-0.5 shrink-0" />
      <span className="first-letter:uppercase">{children}</span>
    </p>
  );
}
