import { useEffect, useState } from "react";
import { Button } from "./Button";

// A question before something that can't simply be taken back (money leaving trust, a payment
// marked bounced, a record archived): a dialog over the page, naming what it does and any
// amount, and nothing is sent until staff confirm. Escape or Cancel sends nothing.
export function ConfirmDialog({
  question,
  confirmLabel = "Confirm",
  danger = false,
  onConfirm,
  onCancel,
}: {
  question: string;
  confirmLabel?: string;
  danger?: boolean;
  onConfirm: () => void;
  onCancel: () => void;
}) {
  useEffect(() => {
    const close = (event: KeyboardEvent) => event.key === "Escape" && onCancel();
    window.addEventListener("keydown", close);
    return () => window.removeEventListener("keydown", close);
  }, [onCancel]);
  return (
    <>
      <div className="fixed inset-0 z-40 bg-scrim" onClick={onCancel} />
      <div role="alertdialog" aria-modal="true" aria-label={question}
        className="fixed top-1/3 left-1/2 z-50 flex w-[440px] max-w-[calc(100vw-2rem)] -translate-x-1/2 flex-col gap-4 rounded-md border border-card-edge bg-canvas p-6 shadow-lg">
        <p className="text-base text-ink">{question}</p>
        <div className="flex justify-end gap-2">
          <Button type="button" variant="secondary" onClick={onCancel}>Cancel</Button>
          <Button type="button" autoFocus variant={danger ? "danger" : "primary"} onClick={onConfirm}>
            {confirmLabel}
          </Button>
        </div>
      </div>
    </>
  );
}

type Pending = { question: string; act: () => unknown; label?: string };

// A step before money leaves trust (a draw, a bill paid, the company's fees taken out, a deposit
// returned): the form asks, naming the amount, and nothing is sent until staff confirm. `ask`
// shows the question; `prompt` is the dialog, which the page renders.
export function useConfirm() {
  const [pending, setPending] = useState<Pending | null>(null);
  const ask = (question: string, act: () => unknown, label?: string) => setPending({ question, act, label });
  const prompt = pending && (
    <ConfirmDialog question={pending.question} confirmLabel={pending.label}
      onConfirm={() => {
        setPending(null);
        void pending.act();
      }}
      onCancel={() => setPending(null)} />
  );
  return { ask, prompt };
}
