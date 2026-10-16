// The property group or property staff narrowed the list pages to (the founder's punch list,
// "property group selector"): in the URL (?group= or ?property=) so a link carries it, and in
// this browser's storage so it carries to the next list page, as Buildium's does.
import { useEffect, useState } from "react";

export type Scope = { group?: string; property?: string };

const KEY = "pms.scope";

export function scopeOf(search: string): Scope {
  const params = new URLSearchParams(search);
  const group = params.get("group");
  const property = params.get("property");
  return group ? { group } : property ? { property } : {};
}

function stored(): Scope {
  try {
    const value: unknown = JSON.parse(window.localStorage.getItem(KEY) ?? "{}");
    if (value && typeof value === "object") {
      const { group, property } = value as Scope;
      return typeof group === "string" ? { group } : typeof property === "string" ? { property } : {};
    }
  } catch {
    // Storage may be off (a private window); the page still works, narrowed by its URL only.
  }
  return {};
}

function store(scope: Scope): void {
  try {
    window.localStorage.setItem(KEY, JSON.stringify(scope));
  } catch {
    // As above: nothing to keep it in.
  }
}

// The scope's own query parameters, to add to a list's API call.
export function scopeParams(scope: Scope): Record<string, string> {
  return scope.group ? { group: scope.group } : scope.property ? { property: scope.property } : {};
}

function inUrl(scope: Scope): void {
  const url = new URL(window.location.href);
  url.searchParams.delete("group");
  url.searchParams.delete("property");
  for (const [key, value] of Object.entries(scopeParams(scope))) url.searchParams.set(key, value);
  window.history.replaceState(null, "", `${url.pathname}${url.search}${url.hash}`);
}

// The page's scope: the URL's, or else the one last chosen in this browser (then put in the URL).
export function useScope(): [Scope, (scope: Scope) => void] {
  const [scope, setScope] = useState<Scope>(() => {
    const fromUrl = scopeOf(window.location.search);
    return fromUrl.group || fromUrl.property ? fromUrl : stored();
  });
  useEffect(() => {
    inUrl(scope);
    store(scope);
  }, [scope]);
  return [scope, setScope];
}
