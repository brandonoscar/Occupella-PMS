import type { InputHTMLAttributes } from "react";

export function Field({
  label,
  id,
  ...input
}: InputHTMLAttributes<HTMLInputElement> & { label: string; id: string }) {
  return (
    <label htmlFor={id} className="flex flex-col gap-1.5 text-sm text-ink-muted">
      {label}
      <input
        id={id}
        className="h-9 rounded-sm border border-line-strong bg-sunken px-3 text-base text-ink placeholder:text-ink-faint"
        {...input}
      />
    </label>
  );
}
