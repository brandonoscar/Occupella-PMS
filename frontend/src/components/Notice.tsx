// A message about what just happened: an error in the danger colour, anything else muted.
export function Notice({ tone, children }: { tone: "error" | "info"; children: React.ReactNode }) {
  if (!children) return null;
  const colour = tone === "error" ? "text-danger" : "text-ink-muted";
  return (
    <p role={tone === "error" ? "alert" : "status"} className={`text-sm ${colour}`}>
      {children}
    </p>
  );
}
