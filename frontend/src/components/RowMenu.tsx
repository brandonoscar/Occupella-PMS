import { MoreHorizontal } from "lucide-react";
import { useState } from "react";
import { Button, Menu, MenuItem, MenuTrigger, Popover } from "react-aria-components";
import { ConfirmDialog } from "./Confirm";

export type RowAction = {
  label: string;
  onSelect: () => unknown;
  // Asked first, naming what it does and any amount ("Mark the $1,395.00 payment bounced?").
  confirm?: string;
  danger?: boolean;
};

// A row's risky actions behind ⋯ (the founder's punch list: It bounced, Archive, Stop, Undo,
// Decline, Disable, Void), each one confirmed before it is sent. React Aria's menu: the arrow
// keys move through it, a letter jumps to an action, Escape or a click outside closes it.
export function RowMenu({ label, actions }: { label: string; actions: RowAction[] }) {
  const [asking, setAsking] = useState<RowAction | null>(null);

  function choose(action: RowAction) {
    if (action.confirm) setAsking(action);
    else void action.onSelect();
  }

  return (
    <>
      <MenuTrigger>
        <Button aria-label={`Actions for ${label}`}
          className="inline-flex h-8 w-8 items-center justify-center rounded-sm text-ink-muted hover:bg-raised hover:text-ink">
          <MoreHorizontal size={16} strokeWidth={1.75} aria-hidden="true" />
        </Button>
        <Popover placement="bottom end"
          className="z-30 min-w-[180px] rounded-md border border-card-edge bg-canvas py-1 shadow-lg">
          <Menu aria-label={`Actions for ${label}`} className="flex flex-col outline-none"
            onAction={(key) => choose(actions[Number(key)]!)}>
            {actions.map((action, n) => (
              <MenuItem key={action.label} id={n} textValue={action.label}
                className={"cursor-default px-3 py-2 text-left text-sm outline-none data-[focused]:bg-raised "
                  + (action.danger ? "text-danger" : "text-ink")}>
                {action.label}
              </MenuItem>
            ))}
          </Menu>
        </Popover>
      </MenuTrigger>
      {asking && (
        <ConfirmDialog question={asking.confirm ?? asking.label} confirmLabel={asking.label}
          danger={asking.danger}
          onConfirm={() => {
            const act = asking;
            setAsking(null);
            void act.onSelect();
          }}
          onCancel={() => setAsking(null)} />
      )}
    </>
  );
}
