import { Home, type LucideIcon, LogOut, Menu, X } from "lucide-react";
import { useEffect, useState } from "react";
import type { Me } from "../api";
import { api } from "../api";
import { navigate, usePath } from "../router";
import { SearchBox } from "./SearchBox";

// The name, set as occupella.com and Occupella's app set it: lowercase "occupella" in Fraunces.
export function Wordmark() {
  return (
    <span className="font-display text-lg leading-none font-semibold tracking-tight text-ink lowercase">
      occupella
    </span>
  );
}

export type NavItem = { href: string; label: string; icon?: LucideIcon };
// A group of the menu (Rentals, Accounting...): its name over the pages in it.
export type NavGroup = { label: string; icon: LucideIcon; items: NavItem[] };
export type NavEntry = NavItem | NavGroup;

function isGroup(entry: NavEntry): entry is NavGroup {
  return "items" in entry;
}

// A menu link is the page shown when the path is its own or one of its pages' (/settings/staff
// is Settings'); the Dashboard, at /, only on its own.
export function isCurrent(href: string, path: string): boolean {
  return href === "/" ? path === "/" : path === href || path.startsWith(`${href}/`);
}

// An owner or a tenant has one page; the rail still names it, as Occupella's always does.
const HOME: NavItem[] = [{ href: "/", label: "Overview", icon: Home }];

// The frame around a signed-in page, laid out as Occupella's app is: a full-height light-blue
// sidebar on the left (the wordmark, the sections and Sign out), a white bar above the content
// with the avatar of who is signed in (their name and the PMC's on it), and the page in a column
// of at most 1100px. Below 768px the sidebar is a drawer the bar's menu button opens.
export function Shell({
  me,
  nav = [],
  children,
}: {
  me: Me;
  nav?: NavEntry[];
  children: React.ReactNode;
}) {
  const path = usePath();
  const [open, setOpen] = useState(false);
  const narrow = useNarrow();
  const items = nav.length > 0 ? nav : HOME;

  useEffect(() => {
    if (!open) return;
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") setOpen(false);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open]);

  async function signOut() {
    await api.post("/api/sign-out");
    navigate(`/sign-in?pmc=${me.pmc_id}`);
  }

  return (
    <div className="flex h-screen overflow-hidden bg-canvas">
      {/* Clicking beside the open menu closes it, as Escape does. */}
      {open && (
        <div
          data-testid="scrim"
          role="presentation"
          className="fixed inset-0 z-40 bg-scrim md:hidden"
          onClick={() => setOpen(false)}
        />
      )}
      <aside
        aria-label="Sidebar"
        data-open={open ? "true" : "false"}
        // A closed drawer stays out of the tab order; the rail beside the page never is.
        inert={narrow && !open}
        className={
          "fixed inset-y-0 z-50 flex w-[260px] shrink-0 flex-col border-r border-chrome-line" +
          " bg-chrome transition-[left] duration-200 ease-out md:static md:z-auto" +
          (open ? " left-0" : " -left-[260px]")
        }
      >
        <div className="flex h-8 shrink-0 items-center justify-between px-6">
          <Wordmark />
          <button
            type="button"
            aria-label="Close menu"
            className="inline-flex h-7 w-7 items-center justify-center rounded-sm text-chrome-ink-muted md:hidden"
            onClick={() => setOpen(false)}
          >
            <X size={16} strokeWidth={1.75} />
          </button>
        </div>
        <nav aria-label="Sections" className="flex min-h-0 flex-1 flex-col gap-px overflow-y-auto px-6 pt-3 pb-3">
          {items.map((entry) =>
            isGroup(entry) ? (
              <div key={entry.label} role="group" aria-label={entry.label} className="flex shrink-0 flex-col gap-px pt-2">
                <span className="flex h-7 items-center gap-2.5 pl-4 text-xs font-medium tracking-wider text-chrome-ink-muted uppercase">
                  <entry.icon size={16} strokeWidth={1.5} aria-hidden="true" />
                  {entry.label}
                </span>
                {entry.items.map((item) => (
                  <RailLink
                    key={item.href}
                    item={item}
                    inset
                    active={isCurrent(item.href, path)}
                    onGo={() => {
                      setOpen(false);
                      navigate(item.href);
                    }}
                  />
                ))}
              </div>
            ) : (
              <RailLink
                key={entry.href}
                item={entry}
                active={isCurrent(entry.href, path)}
                onGo={() => {
                  setOpen(false);
                  navigate(entry.href);
                }}
              />
            ),
          )}
        </nav>
        <div className="border-t border-chrome-line px-6 pt-2 pb-3">
          <button type="button" onClick={signOut} className={`${RAIL_ROW} ${IDLE_ROW}`}>
            <LogOut size={16} strokeWidth={1.5} aria-hidden="true" />
            <span className="flex-1 truncate text-left">Sign out</span>
          </button>
        </div>
      </aside>
      <div className="flex min-w-0 flex-1 flex-col overflow-hidden">
        <header className="flex h-8 shrink-0 items-center gap-2 border-b border-titlebar-line bg-titlebar px-3">
          <button
            type="button"
            aria-label="Open menu"
            className="inline-flex h-7 w-7 items-center justify-center rounded-sm text-ink-muted hover:bg-titlebar-raised md:hidden"
            onClick={() => setOpen(true)}
          >
            <Menu size={16} strokeWidth={1.75} />
          </button>
          <span className="flex flex-1 justify-center px-2">
            {me.kind === "staff" && nav.length > 0 && <SearchBox />}
          </span>
          <span
            role="img"
            aria-label={`${me.name}, ${me.pmc_name}`}
            title={`${me.name}, ${me.pmc_name}`}
            className="mr-1 inline-flex h-6 w-6 items-center justify-center rounded-full bg-iris text-xs font-semibold text-canvas"
          >
            {me.name.trim().charAt(0).toUpperCase() || "?"}
          </span>
        </header>
        <main className="flex-1 overflow-y-auto">
          <div className="mx-auto flex w-full max-w-[1100px] flex-col gap-8 px-6 py-8 md:px-12">
            {children}
          </div>
        </main>
      </div>
    </div>
  );
}

// Below Tailwind's md (768px) the sidebar is a drawer; kept in step with the md: classes.
function useNarrow(): boolean {
  const query = "(max-width: 767px)";
  const [narrow, setNarrow] = useState(
    () => typeof window !== "undefined" && !!window.matchMedia?.(query).matches,
  );
  useEffect(() => {
    const list = window.matchMedia?.(query);
    if (!list) return;
    const onChange = (event: MediaQueryListEvent) => setNarrow(event.matches);
    list.addEventListener("change", onChange);
    setNarrow(list.matches);
    return () => list.removeEventListener("change", onChange);
  }, []);
  return narrow;
}

// A row of the rail, as Occupella's NavItem draws one: 32px, 13px medium; the selected row lifts
// to white with a small shadow and a 3px accent bar at its left.
// A row never shrinks below its 32px when the menu is longer than the screen (it scrolls instead):
// a squeezed row is too small a target to tap (WCAG 2.5.8).
const RAIL_ROW =
  "group relative flex h-8 w-full shrink-0 items-center gap-2.5 rounded-md pr-3 pl-4 text-sm font-medium" +
  " transition-colors duration-150";
// A group's page, its text in line with the group name's (past the 16px icon and its gap).
const INSET_ROW = RAIL_ROW.replace("pl-4", "pl-[42px]");
const IDLE_ROW = "text-chrome-ink-muted hover:bg-chrome-raised hover:text-chrome-ink";
const ACTIVE_ROW = "bg-chrome-raised text-chrome-ink shadow-sm";

function RailLink({
  item,
  active,
  inset = false,
  onGo,
}: {
  item: NavItem;
  active: boolean;
  // A page of a group sits under its name, in line with the group's icon's text.
  inset?: boolean;
  onGo: () => void;
}) {
  const Icon = item.icon;
  return (
    <a
      href={item.href}
      aria-current={active ? "page" : undefined}
      className={`${inset ? INSET_ROW : RAIL_ROW} ${active ? ACTIVE_ROW : IDLE_ROW}`}
      onClick={(event) => {
        // A plain click navigates in place; a click for a new tab or window is the browser's.
        if (event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) {
          return;
        }
        event.preventDefault();
        onGo();
      }}
    >
      <span
        aria-hidden="true"
        className={`absolute top-1 bottom-1 left-1 w-[3px] rounded-full ${active ? "bg-chrome-accent" : ""}`}
      />
      {Icon && <Icon size={16} strokeWidth={1.5} aria-hidden="true" />}
      <span className="flex-1 truncate">{item.label}</span>
    </a>
  );
}

// A page's title, as Occupella's pages open: Fraunces, with one line saying what the page is for,
// and the page's own actions at the right.
export function PageHeader({
  title,
  description,
  actions,
}: {
  title: React.ReactNode;
  description?: React.ReactNode;
  actions?: React.ReactNode;
}) {
  return (
    <header className="flex items-start justify-between gap-4">
      <div className="flex min-w-0 flex-col gap-1">
        <h1 className="font-display text-lg leading-tight font-semibold tracking-tight">{title}</h1>
        {description && <p className="text-sm leading-relaxed text-ink-muted">{description}</p>}
      </div>
      {actions && <div className="flex shrink-0 items-center gap-2">{actions}</div>}
    </header>
  );
}

// A part of a page, as Occupella sets one: a small uppercase label over its content, no box
// around it. Tables and lists draw their own hairlines.
export function Card({ title, children }: { title: React.ReactNode; children: React.ReactNode }) {
  return (
    <section className="flex flex-col gap-3">
      <h2 className="text-xs font-medium tracking-wider text-ink-subtle uppercase">{title}</h2>
      <div>{children}</div>
    </section>
  );
}
