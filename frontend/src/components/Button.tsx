import { Loader2 } from "lucide-react";
import type { ButtonHTMLAttributes, MouseEvent } from "react";
import { useId, useState } from "react";
import { useFormBusy } from "./Form";

type Variant = "primary" | "secondary" | "ghost" | "danger";

// Occupella's buttons: primary solid iris with white text, secondary white with a strong
// hairline, ghost an iris text link. All 36px tall with the 6px radius; disabled is grey.
const VARIANTS: Record<Variant, string> = {
  primary:
    "border border-iris bg-iris text-canvas hover:border-iris-hover hover:bg-iris-hover" +
    " active:bg-iris-press",
  secondary: "border border-line-strong bg-canvas text-ink hover:bg-raised",
  ghost: "border border-transparent text-iris underline-offset-2 hover:text-iris-hover hover:underline",
  // The confirm of something that can't simply be taken back.
  danger: "border border-danger bg-danger text-canvas hover:opacity-90",
};

// A row action inside a table or a list is the small size: 32px, 13px.
const SIZES = { md: "h-9 px-3.5 text-base", sm: "h-8 px-3 text-sm" };

// A button is busy while what it started runs: its form's save (<Form>), or the promise its own
// click returned. Busy, it shows a spinner and can't be pressed again.
//
// `says` is the one plain line under an action button (ADR 0010, rule 1): what pressing it does
// and what it doesn't ("Records the payment. Nothing is sent to the tenant."). The button is
// described by it, so a screen reader reads it too. buttonRules.test.ts holds every submit button
// to having one.
export function Button({
  variant = "primary",
  size = "md",
  className = "",
  busy,
  onClick,
  disabled,
  says,
  children,
  ...props
}: Omit<ButtonHTMLAttributes<HTMLButtonElement>, "onClick"> & {
  variant?: Variant;
  size?: keyof typeof SIZES;
  busy?: boolean;
  onClick?: (event: MouseEvent<HTMLButtonElement>) => unknown;
  says?: string;
}) {
  const formBusy = useFormBusy();
  const [clicked, setClicked] = useState(false);
  const saysId = useId();
  const working = busy ?? (clicked || (props.type === "submit" && formBusy));
  const base =
    "inline-flex items-center justify-center gap-1.5 rounded-sm font-medium whitespace-nowrap" +
    " transition-colors duration-100 disabled:border-disabled-bg disabled:bg-disabled-bg" +
    " disabled:text-disabled-ink";
  const button = (
    <button
      className={`${base} ${SIZES[size]} ${VARIANTS[variant]} ${className}`}
      disabled={disabled || working}
      aria-busy={working || undefined}
      aria-describedby={says ? saysId : undefined}
      onClick={(event) => {
        const result = onClick?.(event);
        if (result instanceof Promise) {
          setClicked(true);
          result.finally(() => setClicked(false));
        }
      }}
      {...props}
    >
      {working && <Loader2 size={14} strokeWidth={2} aria-hidden="true" className="animate-spin" />}
      {children}
    </button>
  );
  if (!says) return button;
  return (
    <span className="inline-flex flex-col items-start gap-1">
      {button}
      <span id={saysId} data-testid="button-says" className="text-sm text-ink-muted">{says}</span>
    </span>
  );
}

export function LinkButton({
  href,
  children,
}: {
  href: string;
  children: React.ReactNode;
}) {
  return (
    <a
      href={href}
      className="inline-flex h-9 items-center rounded-sm border border-line-strong bg-canvas px-3.5 text-base font-medium text-ink hover:bg-raised"
    >
      {children}
    </a>
  );
}
