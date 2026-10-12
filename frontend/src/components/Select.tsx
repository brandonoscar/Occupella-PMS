import type { SelectHTMLAttributes } from "react";

export function Select({
  label,
  id,
  children,
  ...select
}: SelectHTMLAttributes<HTMLSelectElement> & { label: string; id: string }) {
  return (
    <label htmlFor={id} className="flex flex-col gap-1.5 text-sm text-ink-muted">
      {label}
      <select
        id={id}
        className="h-9 rounded-sm border border-line-strong bg-sunken px-2 text-sm text-ink"
        {...select}
      >
        {children}
      </select>
    </label>
  );
}
