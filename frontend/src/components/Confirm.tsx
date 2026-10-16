import { useState } from "react";
import { Button } from "./Button";

type Pending = { question: string; act: () => unknown };

// A step before money leaves trust (a draw, a bill paid, the company's fees taken out, a deposit
// returned): the form asks, naming the amount, and nothing is sent until staff confirm. `ask`
// shows the question; `prompt` is where the page puts it, next to the form that asked.
export function useConfirm() {
  const [pending, setPending] = useState<Pending | null>(null);
  const ask = (question: string, act: () => unknown) => setPending({ question, act });
  const prompt = pending && (
    <div role="alertdialog" aria-label={pending.question}
      className="flex flex-wrap items-center gap-3 rounded-md border border-card-edge bg-raised px-4 py-3">
      <p className="text-base text-ink">{pending.question}</p>
      <div className="flex gap-2">
        <Button type="button" onClick={() => {
          setPending(null);
          void pending.act();
        }}>
          Confirm
        </Button>
        <Button type="button" variant="secondary" onClick={() => setPending(null)}>Cancel</Button>
      </div>
    </div>
  );
  return { ask, prompt };
}
