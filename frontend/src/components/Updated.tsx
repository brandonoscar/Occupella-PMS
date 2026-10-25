import { useEffect, useState } from "react";

// A count that goes up each time the browser tab comes back into view or its window takes focus
// again (ADR 0010 phase 3, part M): a page that reads its ledger again on it never leaves one
// open in a tab passing for current. Showing a tab can fire both; the page's `latest()` keeps the
// last answer.
export function useAgainOnFocus(): number {
  const [times, setTimes] = useState(0);
  useEffect(() => {
    function again() {
      if (document.visibilityState === "visible") setTimes((n) => n + 1);
    }
    window.addEventListener("focus", again);
    document.addEventListener("visibilitychange", again);
    return () => {
      window.removeEventListener("focus", again);
      document.removeEventListener("visibilitychange", again);
    };
  }, []);
  return times;
}

// "02:05 PM": the time on the viewer's own clock.
function clock(at: Date): string {
  return at.toLocaleTimeString("en-US", { hour: "2-digit", minute: "2-digit" });
}

// "Updated 02:05 PM": when what the page shows was last read.
export function UpdatedStamp({ at }: { at: Date | null }) {
  if (at === null) return null;
  return <span className="text-sm text-ink-subtle" data-testid="updated">Updated {clock(at)}</span>;
}
