import { useEffect, useRef, useState } from "react";

// A table too wide for a phone (a ledger's money columns, five columns with a field in one):
// there it scrolls sideways inside its own frame, and the page doesn't. While it does, the frame
// is a region named after its table that takes keyboard focus, so the arrow keys scroll it too
// (WCAG 2.1.1; axe's scrollable-region-focusable, found by the phone-size browser test, q124);
// a table that fits adds no stop to the tab order.
export function Wide({ children }: { children: React.ReactNode }) {
  const frame = useRef<HTMLDivElement>(null);
  const [scrolls, setScrolls] = useState(false);
  const [name, setName] = useState("");
  useEffect(() => {
    const el = frame.current;
    if (!el) return;
    const measure = () => {
      setScrolls(el.scrollWidth > el.clientWidth);
      const table = el.querySelector("table");
      setName(table?.getAttribute("aria-label") ?? table?.querySelector("caption")?.textContent ?? "");
    };
    measure();
    if (typeof ResizeObserver === "undefined") return;
    const watch = new ResizeObserver(measure);
    watch.observe(el);
    if (el.firstElementChild) watch.observe(el.firstElementChild);
    return () => watch.disconnect();
  }, []);
  return scrolls ? (
    <div ref={frame} className="overflow-x-auto" role="region" aria-label={`${name || "Table"}, scrolls sideways`} tabIndex={0}>
      {children}
    </div>
  ) : (
    <div ref={frame} className="overflow-x-auto">{children}</div>
  );
}
