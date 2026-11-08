// A small router: the path picks the page. Links and redirects go through navigate(), so the
// page changes without a reload. The pages name root paths ("/properties"); the portal's own
// path (src/base.ts, /books/ in Occupella's hosted app) is added here on the way out and taken
// off by usePath() on the way in.
import { useEffect, useState } from "react";
import { withBase, withoutBase } from "./base";

export function navigate(to: string): void {
  const target = withBase(to);
  // A link inside a clickable row asks twice; the page it is on already adds nothing to the
  // history, so Back goes back one page.
  const here = window.location.pathname + window.location.search + window.location.hash;
  if (target !== here) window.history.pushState(null, "", target);
  window.dispatchEvent(new PopStateEvent("popstate"));
}

export function usePath(): string {
  const [path, setPath] = useState(withoutBase(window.location.pathname));
  useEffect(() => {
    const changed = () => setPath(withoutBase(window.location.pathname));
    window.addEventListener("popstate", changed);
    return () => window.removeEventListener("popstate", changed);
  }, []);
  return path;
}

// Leaves the page for another, such as the signing page a tenant's new link opens or the
// payments partner's page. Another site's address goes as it is.
export function leave(url: string): void {
  window.location.assign(withBase(url));
}
