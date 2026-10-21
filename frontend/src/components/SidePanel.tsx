import { X } from "lucide-react";
import { Dialog, Heading, Modal, ModalOverlay } from "react-aria-components";

// A form that opens beside the page (ADR 0005: "Record bill opens a side panel"): a dialog at
// the right over a scrim, its title at the top and Close; Escape or the scrim closes it too.
// React Aria keeps focus inside it while it is open, hides the page behind from screen readers
// and gives focus back to what opened it.
export function SidePanel({
  title,
  onClose,
  children,
}: {
  title: string;
  onClose: () => void;
  children: React.ReactNode;
}) {
  return (
    <ModalOverlay isOpen isDismissable onOpenChange={(open) => open || onClose()}
      data-testid="panel-scrim" className="fixed inset-0 z-40 bg-scrim">
      <Modal className="fixed inset-y-0 right-0 z-50 flex w-full max-w-md border-l border-card-edge bg-canvas shadow-lg">
        <Dialog className="flex w-full flex-col gap-4 overflow-y-auto p-6 outline-none">
          <div className="flex items-center justify-between gap-3">
            <Heading slot="title" className="font-display text-xl text-ink">{title}</Heading>
            <button type="button" aria-label="Close" onClick={onClose}
              className="rounded-sm p-1 text-ink-muted hover:bg-raised hover:text-ink">
              <X size={16} strokeWidth={1.75} aria-hidden="true" />
            </button>
          </div>
          {children}
        </Dialog>
      </Modal>
    </ModalOverlay>
  );
}
