import { Search } from "lucide-react";
import { useEffect, useState } from "react";
import { api } from "../api";
import type { Found } from "../rentals";
import { navigate } from "../router";

const KIND: Record<Found["kind"], string> = {
  property: "Property",
  unit: "Unit",
  tenant: "Tenant",
  owner: "Owner",
};

// Staff's search at the top of every page: a property, unit, tenant or owner by name, from two
// letters; choosing one opens its page.
export function SearchBox() {
  const [q, setQ] = useState("");
  const [found, setFound] = useState<Found[]>([]);

  useEffect(() => {
    const words = q.trim();
    if (words.length < 2) {
      setFound([]);
      return;
    }
    let live = true;
    const wait = setTimeout(() => {
      api
        .get<Found[]>(`/api/search?${new URLSearchParams({ q: words })}`)
        .then((answer) => live && setFound(answer))
        .catch(() => live && setFound([]));
    }, 150);
    return () => {
      live = false;
      clearTimeout(wait);
    };
  }, [q]);

  function go(href: string) {
    setQ("");
    setFound([]);
    navigate(href);
  }

  return (
    <div className="relative w-full max-w-sm">
      <Search size={14} strokeWidth={1.75} aria-hidden="true"
        className="pointer-events-none absolute top-1/2 left-2.5 -translate-y-1/2 text-ink-subtle" />
      <input
        type="search"
        aria-label="Search properties, units, tenants and owners"
        placeholder="Search properties, units, tenants and owners"
        className="h-7 w-full rounded-sm border border-line-strong bg-sunken pr-2 pl-8 text-sm text-ink placeholder:text-ink-faint"
        value={q}
        onChange={(event) => setQ(event.target.value)}
        onKeyDown={(event) => {
          if (event.key === "Enter" && found[0]) go(found[0].href);
          if (event.key === "Escape") setQ("");
        }}
      />
      {found.length > 0 && (
        <ul aria-label="Search results"
          className="absolute top-8 right-0 left-0 z-30 max-h-80 overflow-y-auto rounded-md border border-card-edge bg-canvas py-1 shadow-sm">
          {found.map((item) => (
            <li key={`${item.kind}-${item.id}`}>
              <a
                href={item.href}
                className="flex items-baseline justify-between gap-3 px-3 py-2 text-sm text-ink hover:bg-raised"
                onClick={(event) => {
                  if (event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey) return;
                  event.preventDefault();
                  go(item.href);
                }}
              >
                <span>
                  {item.label}
                  {item.detail && <span className="text-ink-muted"> · {item.detail}</span>}
                </span>
                <span className="text-xs text-ink-subtle">{KIND[item.kind]}</span>
              </a>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
