// A table too wide for a phone (a ledger's money columns, five columns with a field in one):
// there it scrolls sideways inside its own frame, and the page doesn't.
export function Wide({ children }: { children: React.ReactNode }) {
  return <div className="overflow-x-auto">{children}</div>;
}
