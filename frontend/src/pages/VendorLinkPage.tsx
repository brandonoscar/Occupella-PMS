import { useEffect, useState } from "react";
import { ApiError, api } from "../api";
import { Badge } from "../components/Badge";
import { Button } from "../components/Button";
import { useConfirm } from "../components/Confirm";
import { Field } from "../components/Field";
import { Form } from "../components/Form";
import { Notice } from "../components/Notice";
import { Said } from "../components/Said";
import { Card, Wordmark } from "../components/Shell";
import { usDate } from "../money";
import type { VendorView, VendorWorkOrder } from "../workOrders";
import { PRIORITY } from "../workOrders";

function detail(failed: unknown): string {
  return failed instanceof ApiError ? failed.detail : String(failed);
}

// A vendor's work orders: /vendor#<token>, the link the company sent them. No login: the token
// travels in the request's body. They schedule each visit and say when the work is done.
export function VendorLinkPage() {
  const token = window.location.hash.slice(1);
  const [view, setView] = useState<VendorView | null>(null);
  const [error, setError] = useState("");
  const [said, setSaid] = useState("");
  useEffect(() => {
    api.post<VendorView>("/api/vendor-link/work-orders", { token }).then(setView).catch((failed) => setError(detail(failed)));
  }, [token]);
  async function step(order: VendorWorkOrder, body: Record<string, string | null>, done: string) {
    setError("");
    setSaid("");
    try {
      setView(await api.post<VendorView>(`/api/vendor-link/work-orders/${order.id}/steps`, { token, ...body }));
      setSaid(done);
    } catch (failed) {
      setError(detail(failed));
    }
  }
  return (
    <div className="mx-auto flex max-w-2xl flex-col gap-6 px-6 py-10">
      <Wordmark />
      {view === null ? (
        <Notice tone={error ? "error" : "info"}>{error || "Loading"}</Notice>
      ) : (
        <>
          <h1 className="font-display text-xl text-ink">{view.vendor}: work orders from {view.company}</h1>
          <Said>{said}</Said>
          <Notice tone="error">{error}</Notice>
          {view.work_orders.length === 0 ? (
            <Notice tone="info">No work order is waiting for you.</Notice>
          ) : (
            view.work_orders.map((order) => <VendorOrder key={order.id} order={order} step={step} />)
          )}
        </>
      )}
    </div>
  );
}

type Step = (order: VendorWorkOrder, body: Record<string, string | null>, done: string) => Promise<void>;

function VendorOrder({ order, step }: { order: VendorWorkOrder; step: Step }) {
  const [on, setOn] = useState(order.visit_on ?? "");
  const [slot, setSlot] = useState(order.visit_window ?? "");
  const [note, setNote] = useState("");
  const { ask, prompt } = useConfirm();
  const where = order.unit ? `${order.unit}, ${order.property}` : order.property;
  return (
    <Card title={`#${order.number} ${order.summary}`}>
      <section aria-label={order.summary} className="flex flex-col gap-3">
        <p className="flex flex-wrap items-center gap-2 text-base text-ink">
          {where}
          <Badge tone={PRIORITY[order.priority].tone}>{PRIORITY[order.priority].word}</Badge>
        </p>
        {order.description && <p className="text-base whitespace-pre-line">{order.description}</p>}
        <p className="text-sm text-ink-subtle">
          {order.due_on ? `Due by ${usDate(order.due_on)}. ` : ""}
          {order.entry ?? ""}
        </p>
        {order.visit_on && (
          <p className="text-sm text-ink" data-testid="visit">
            Visit on {usDate(order.visit_on)}{order.visit_window ? `, ${order.visit_window}` : ""}
          </p>
        )}
        <Form className="flex flex-wrap items-end gap-2" onSubmit={(event) => {
          event.preventDefault();
          return step(order, { step: "scheduled", scheduled_for: on, visit_window: slot.trim() || null },
            `Visit for #${order.number} on ${usDate(on)}${slot.trim() ? `, ${slot.trim()}` : ""}.`);
        }}>
          <Field label="Visit on" id={`visit-${order.id}`} type="date" required value={on} onChange={(e) => setOn(e.target.value)} />
          <Field label="Window (optional)" id={`window-${order.id}`} maxLength={60} value={slot} onChange={(e) => setSlot(e.target.value)} />
          <Button type="submit" variant="secondary"
            says={`The company and any tenant who asked for the repair see the ${order.visit_on ? "new " : ""}day. No email is sent.`}>
            {order.visit_on ? "Change the visit" : "Schedule the visit"}
          </Button>
        </Form>
        <Form className="flex flex-wrap items-end gap-2" onSubmit={(event) => {
          event.preventDefault();
          ask(`Mark #${order.number} done? It closes the work order and leaves your list; it can't be reopened.`,
            () => step(order, { step: "completed", note: note.trim() || null }, `#${order.number} is done. Thank you.`),
            "The work is done");
        }}>
          <Field label="What you did (optional)" id={`note-${order.id}`} maxLength={2000} value={note} onChange={(e) => setNote(e.target.value)} />
          <Button type="submit"
            says="Tells the company it's done and closes the work order; it leaves this list and can't be reopened.">
            The work is done
          </Button>
        </Form>
      </section>
      {prompt}
    </Card>
  );
}
