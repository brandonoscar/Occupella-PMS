// Where the portal is served: at the root of its own address when self-hosted, under a path
// when an app mounts it (Occupella's hosted app serves it at /books/, built with
// `vite build --base /books/`). Its pages and its API share that path: /books/properties,
// /books/api/me.
//
// Every URL that reaches the browser goes through withBase() where it is used: a link's href,
// an image's src, fetch(), navigate() and leave(). The paths the code writes stay root paths
// ("/properties", "/api/me"), and the pages compare usePath()'s answer without it.
// src/base.test.ts checks every such place.

/** The path the portal is served under, without its last slash: "" at the root, "/books". */
function basePath(): string {
  const base = import.meta.env.BASE_URL || "/";
  return base === "/" ? "" : `/${base.replace(/^\/+|\/+$/g, "")}`;
}

/** A root path ("/properties", "/api/me") under the portal's path. Anything else (another
 * site's address, mailto:, a fragment) and a path already under it come back unchanged, so
 * passing a URL through twice changes nothing. */
export function withBase(url: string): string {
  const base = basePath();
  if (!base || !url.startsWith("/") || url.startsWith("//")) return url;
  if (url === base || url.startsWith(`${base}/`) || url.startsWith(`${base}?`) || url.startsWith(`${base}#`)) {
    return url;
  }
  return `${base}${url}`;
}

/** The page a browser's path names, without the portal's path: "/books/properties" is
 * "/properties", "/books" and "/books/" are "/". A path outside it comes back unchanged. */
export function withoutBase(pathname: string): string {
  const base = basePath();
  if (!base) return pathname;
  if (pathname === base) return "/";
  return pathname.startsWith(`${base}/`) ? pathname.slice(base.length) : pathname;
}
