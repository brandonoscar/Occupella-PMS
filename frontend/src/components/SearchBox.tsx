import { Search } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { api } from "../api";
import type { Found } from "../rentals";
import { navigate } from "../router";

const KIND: Record<Found["kind"], string> = {
  property: "Property",
  unit: "Unit",
  tenant: "Tenant",
  owner: "Owner",
  vendor: "Vendor",
  bill: "Bill",
  "work order": "Work order",
  applicant: "Applicant",
  lead: "Lead",
  file: "File",
};

// Staff's search at the top of every page, from two letters: a property, unit, tenant, owner or
// vendor by name or by the email, phone or street on file, a bill, a work order, an applicant, a
// lead or a file; choosing one opens its page. The results are links (a new tab, a copied link), and the
// arrow keys move from the box through them; Escape clears the search and goes back to the box.
export function SearchBox() {
  const [q, setQ] = useState("");
  const [found, setFound] = useState<Found[]>([]);
  const input = useRef<HTMLInputElement>(null);
  const list = useRef<HTMLUListElement>(null);

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

  // The nth result's link, counting from 0; past the ends it is the search box again.
  function focusResult(n: number) {
    const links = [...(list.current?.querySelectorAll("a") ?? [])];
    (n >= 0 && n < links.length ? links[n] : input.current)?.focus();
  }

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
        ref={input}
        type="search"
        aria-label="Search people, homes, bills, work orders and files"
        placeholder="Search people, homes, bills, work orders and files"
        className="h-7 w-full rounded-sm border border-line-strong bg-sunken pr-2 pl-8 text-sm text-ink placeholder:text-ink-faint"
        value={q}
        onChange={(event) => setQ(event.target.value)}
        onKeyDown={(event) => {
          if (event.key === "Enter" && found[0]) go(found[0].href);
          if (event.key === "Escape") setQ("");
          if (event.key === "ArrowDown" && found.length > 0) {
            event.preventDefault();
            focusResult(0);
          }
        }}
      />
      {found.length > 0 && (
        <ul ref={list} aria-label="Search results"
          className="absolute top-8 right-0 left-0 z-30 max-h-80 overflow-y-auto rounded-md border border-card-edge bg-canvas py-1 shadow-sm">
          {found.map((item, n) => (
            <li key={`${item.kind}-${item.id}`}>
              <a
                href={item.href}
                className="flex items-baseline justify-between gap-3 px-3 py-2 text-sm text-ink hover:bg-raised"
                onClick={(event) => {
                  if (event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey) return;
                  event.preventDefault();
                  go(item.href);
                }}
                onKeyDown={(event) => {
                  if (event.key === "ArrowDown" || event.key === "ArrowUp") {
                    event.preventDefault();
                    focusResult(event.key === "ArrowDown" ? n + 1 : n - 1);
                  }
                  if (event.key === "Escape") {
                    setQ("");
                    input.current?.focus();
                  }
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
