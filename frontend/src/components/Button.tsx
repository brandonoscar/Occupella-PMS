import type { ButtonHTMLAttributes } from "react";

type Variant = "primary" | "secondary" | "ghost";

const VARIANTS: Record<Variant, string> = {
  primary: "bg-iris text-ink hover:bg-iris-hover active:bg-iris-press",
  secondary: "border border-line-strong bg-raised text-ink hover:bg-sunken",
  ghost: "text-ink-muted underline-offset-2 hover:text-ink hover:underline",
};

export function Button({
  variant = "primary",
  className = "",
  ...props
}: ButtonHTMLAttributes<HTMLButtonElement> & { variant?: Variant }) {
  const base =
    "inline-flex h-9 items-center justify-center rounded-sm px-4 text-sm font-medium" +
    " transition-colors duration-100 disabled:cursor-not-allowed disabled:bg-disabled-bg" +
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
      className="inline-flex h-9 items-center rounded-sm border border-line-strong bg-raised px-4 text-sm font-medium text-ink hover:bg-sunken"
    >
      {children}
    </a>
  );
}
