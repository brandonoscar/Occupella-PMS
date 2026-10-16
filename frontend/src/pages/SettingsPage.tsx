import { ChevronRight } from "lucide-react";
import type { Me } from "../api";
import { PageHeader, Shell } from "../components/Shell";
import { GoLink } from "./NeedsAttention";
import { SETTINGS, STAFF_NAV } from "./staffNav";

// Settings (ADR 0005): who works here and may sign in, the company's setup, and bringing a
// company over from another PMS, each its own page.
export function SettingsPage({ me }: { me: Me }) {
  return (
    <Shell me={me} nav={STAFF_NAV}>
      <PageHeader title="Settings" />
      <ul aria-label="Settings" className="divide-y divide-line rounded-md border border-card-edge">
        {SETTINGS.map((item) => (
          <li key={item.href}>
            <GoLink
              href={item.href}
              className="flex items-center justify-between px-4 py-3 text-base text-ink hover:bg-raised"
            >
              {item.label}
              <ChevronRight size={16} strokeWidth={1.5} aria-hidden="true" className="text-ink-subtle" />
            </GoLink>
          </li>
        ))}
      </ul>
    </Shell>
  );
}
