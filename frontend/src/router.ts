// A small router: the path picks the page. Links and redirects go through navigate(), so the
// page changes without a reload.
import { useEffect, useState } from "react";

export function navigate(to: string): void {
  window.history.pushState(null, "", to);
  window.dispatchEvent(new PopStateEvent("popstate"));
}

export function usePath(): string {
  const [path, setPath] = useState(window.location.pathname);
  useEffect(() => {
    const changed = () => setPath(window.location.pathname);
    window.addEventListener("popstate", changed);
    return () => window.removeEventListener("popstate", changed);
  }, []);
  return path;
}

// Leaves the page for another, such as the signing page a tenant's new link opens.
export function leave(url: string): void {
  window.location.assign(url);
}
