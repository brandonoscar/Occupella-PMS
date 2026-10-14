import type { ButtonHTMLAttributes } from "react";

type Variant = "primary" | "secondary" | "ghost";

// Occupella's buttons: primary solid iris with white text, secondary white with a strong
// hairline, ghost an iris text link. All 36px tall with the 6px radius; disabled is grey.
const VARIANTS: Record<Variant, string> = {
  primary:
    "border border-iris bg-iris text-canvas hover:border-iris-hover hover:bg-iris-hover" +
    " active:bg-iris-press",
  secondary: "border border-line-strong bg-canvas text-ink hover:bg-raised",
  ghost: "border border-transparent text-iris underline-offset-2 hover:text-iris-hover hover:underline",
};

export function Button({
  variant = "primary",
  className = "",
  ...props
}: ButtonHTMLAttributes<HTMLButtonElement> & { variant?: Variant }) {
  const base =
    "inline-flex h-9 items-center justify-center rounded-sm px-3.5 text-base font-medium" +
    " transition-colors duration-100 disabled:border-disabled-bg disabled:bg-disabled-bg" +
    " disabled:text-disabled-ink";
  return <button className={`${base} ${VARIANTS[variant]} ${className}`} {...props} />;
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
