import type { FormEvent, FormHTMLAttributes } from "react";
import { createContext, useContext, useState } from "react";

const Busy = createContext(false);

// A form whose submit button shows it is working while the save runs, and can't be pressed
// twice: the founder's punch list ("buttons show a loading state"). Its handler returns the
// save's promise, as every async handler does.
export function Form({
  onSubmit,
  ...form
}: Omit<FormHTMLAttributes<HTMLFormElement>, "onSubmit"> & {
  onSubmit?: (event: FormEvent<HTMLFormElement>) => unknown;
}) {
  const [busy, setBusy] = useState(false);
  return (
    <Busy.Provider value={busy}>
      <form
        {...form}
        aria-busy={busy || undefined}
        onSubmit={(event) => {
          if (busy) {
            event.preventDefault();
            return;
          }
          const result = onSubmit?.(event);
          if (result instanceof Promise) {
            setBusy(true);
            result.finally(() => setBusy(false));
          }
        }}
      />
    </Busy.Provider>
  );
}

export function useFormBusy(): boolean {
  return useContext(Busy);
}
