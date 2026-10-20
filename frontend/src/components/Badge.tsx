export type BadgeTone = "neutral" | "positive" | "caution" | "danger" | "info";

// Occupella's status pill: a state word (paid up, bounced, approved) in a small rounded badge,
// never loose coloured text. Each tone is one of the status colours, kept for real states. The
// caution colour reads 4.2:1 on its own tint, under WCAG AA's 4.5:1 for 12px text, so its pill
// is white (4.7:1) with a caution border.
const TONES: Record<BadgeTone, string> = {
  neutral: "border-line bg-elevated text-ink-muted",
  positive: "border-positive-bg bg-positive-bg text-positive",
  caution: "border-caution bg-canvas text-caution",
  danger: "border-danger bg-danger-bg text-danger",
  info: "border-info-bg bg-info-bg text-info",
};

export function Badge({
  tone = "neutral",
  children,
  "data-testid": testId,
}: {
  tone?: BadgeTone;
  children: React.ReactNode;
  "data-testid"?: string;
}) {
  return (
    <span
      data-tone={tone}
      data-testid={testId}
      className={
        "inline-flex h-5 shrink-0 items-center rounded-full border px-2 text-xs font-medium" +
        ` tracking-[0.02em] whitespace-nowrap ${TONES[tone]}`
      }
    >
      {/* A state word from the API stays as it came; only its first letter is shown as a capital. */}
      <span className="first-letter:uppercase">{children}</span>
    </span>
  );
}
