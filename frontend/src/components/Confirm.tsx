import { useState } from "react";
import { Dialog, Modal, ModalOverlay } from "react-aria-components";
import { Button } from "./Button";

// A question before something that can't simply be taken back (money leaving trust, a payment
// marked bounced, a record archived): a dialog over the page, naming what it does and any
// amount, and nothing is sent until staff confirm. Escape, the scrim or Cancel sends nothing.
// React Aria keeps focus inside it and gives it back to what asked.
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
  return (
    <ModalOverlay isOpen isDismissable onOpenChange={(open) => open || onCancel()}
      className="fixed inset-0 z-40 bg-scrim">
      <Modal className="fixed top-1/3 left-1/2 z-50 w-[440px] max-w-[calc(100vw-2rem)] -translate-x-1/2 rounded-md border border-card-edge bg-canvas p-6 shadow-lg">
        <Dialog role="alertdialog" aria-label={question} className="flex flex-col gap-4 outline-none">
          <p className="text-base text-ink">{question}</p>
          <div className="flex justify-end gap-2">
            <Button type="button" variant="secondary" onClick={onCancel}>Cancel</Button>
            <Button type="button" autoFocus variant={danger ? "danger" : "primary"} onClick={onConfirm}>
              {confirmLabel}
            </Button>
          </div>
        </Dialog>
      </Modal>
    </ModalOverlay>
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
