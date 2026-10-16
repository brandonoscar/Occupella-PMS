import { useEffect } from "react";
import { notify } from "../toast";

// What a save did ("Recorded the payment of $1,395.00"): a toast for the eye, and a status line
// a screen reader announces, kept off the screen so it isn't shown twice.
export function Said({ children }: { children: string }) {
  useEffect(() => notify("success", children), [children]);
  if (!children) return null;
  return (
    <p role="status" className="sr-only first-letter:uppercase">
      {children}
    </p>
  );
}
