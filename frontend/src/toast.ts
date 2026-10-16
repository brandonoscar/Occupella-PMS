// What just happened, shown for a few seconds at the corner of every page (the founder's punch
// list: "every save gives feedback"). A save's message comes through <Said> and a refused change
// through api.post; <Toaster> shows them.
import { useSyncExternalStore } from "react";

export type Toast = { id: number; tone: "success" | "error"; text: string };

const SHOWN_FOR = { success: 5000, error: 8000 };
let toasts: Toast[] = [];
let next = 1;
const listeners = new Set<() => void>();

function changed(to: Toast[]): void {
  toasts = to;
  for (const listener of listeners) listener();
}

export function notify(tone: Toast["tone"], text: string): void {
  if (!text) return;
  const toast = { id: next++, tone, text };
  changed([...toasts, toast].slice(-4));
  setTimeout(() => dismiss(toast.id), SHOWN_FOR[tone]);
}

export function dismiss(id: number): void {
  changed(toasts.filter((toast) => toast.id !== id));
}

export function useToasts(): Toast[] {
  return useSyncExternalStore(
    (listener) => {
      listeners.add(listener);
      return () => listeners.delete(listener);
    },
    () => toasts,
  );
}
