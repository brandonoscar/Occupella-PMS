import type { Paged } from "../rentals";
import { hasNext, pageLabel } from "../rentals";
import { Button } from "./Button";

// A list's place in its pages, and the way to the one before or after.
export function Pager({ page, onPage }: { page: Paged<unknown>; onPage: (n: number) => void }) {
  return (
    <nav aria-label="Pages" className="flex items-center justify-end gap-3 text-sm text-ink-muted">
      <span>{pageLabel(page)}</span>
      <Button variant="secondary" size="sm" disabled={page.page <= 1} onClick={() => onPage(page.page - 1)}>
        Previous
      </Button>
      <Button variant="secondary" size="sm" disabled={!hasNext(page)} onClick={() => onPage(page.page + 1)}>
        Next
      </Button>
    </nav>
  );
}
