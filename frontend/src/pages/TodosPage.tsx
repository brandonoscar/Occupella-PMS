import { ListTodo } from "lucide-react";
import { useEffect, useState } from "react";
import type { Me } from "../api";
import { ApiError, api } from "../api";
import { Badge } from "../components/Badge";
import { Button } from "../components/Button";
import { EmptyState } from "../components/EmptyState";
import { Field } from "../components/Field";
import { Form } from "../components/Form";
import { Notice } from "../components/Notice";
import { Pager } from "../components/Pager";
import type { Groups } from "../components/ScopePicker";
import { Said } from "../components/Said";
import { Select } from "../components/Select";
import { PageHeader, Shell } from "../components/Shell";
import { SidePanel } from "../components/SidePanel";
import { Tabs } from "../components/Tabs";
import { Wide } from "../components/Wide";
import { latest } from "../latest";
import { day, usDate } from "../money";
import type { Paged } from "../rentals";
import type { Member } from "../staff";
import type { Todo } from "../todos";
import { todoStanding } from "../todos";
import { STAFF_NAV } from "./staffNav";
import type { Recorder } from "./useRecord";
import { useRecord } from "./useRecord";

type Show = "open" | "done" | "all";
const TABS: { value: Show; label: string }[] = [
  { value: "open", label: "Open" },
  { value: "done", label: "Done" },
  { value: "all", label: "All" },
];
const NONE: Record<Show, string> = {
  open: "Nothing to do.",
  done: "Nothing done yet.",
  all: "No to-do yet.",
};

// To-dos (migration 20261023000203): what staff write down to do, maybe about a property, each
// assigned to someone with the day it is due; the open ones soonest first, then those done. Any
// staff member adds one, reassigns it or moves its day, and marks it done. Each person's own due
// within a week show on the Dashboard.
export function TodosPage({ me }: { me: Me }) {
  const [show, setShow] = useState<Show>("open");
  const [mine, setMine] = useState(false);
  const [number, setNumber] = useState(1);
  const [page, setPage] = useState<Paged<Todo> | null>(null);
  const [loadError, setLoadError] = useState("");
  const [panel, setPanel] = useState<"" | "add" | Todo>("");
  const [tick, setTick] = useState(0);
  const { said, error, record } = useRecord(() => setTick((t) => t + 1));
  const recordThenClose: Recorder = async (path, body, done) => {
    const recorded = await record(path, body, done);
    if (recorded) setPanel("");
    return recorded;
  };

  useEffect(() => {
    const run = latest();
    const query = new URLSearchParams({ status: show, mine: String(mine), page: String(number) });
    api.get<Paged<Todo>>(`/api/todos?${query}`).then(run.keep(setPage))
      .catch(run.keep((why) => setLoadError(why instanceof ApiError ? why.detail : String(why))));
    return run.stop;
  }, [show, mine, number, tick]);

  return (
    <Shell me={me} nav={STAFF_NAV}>
      <PageHeader title="To-dos" actions={<Button onClick={() => setPanel("add")}>Add to-do</Button>} />
      <Said>{said}</Said>
      {panel === "" && <Notice tone="error">{error}</Notice>}
      <div>
        <div className="flex flex-wrap items-end justify-between gap-3">
          <Tabs label="To-dos" tabs={TABS} value={show} onChange={(next) => { setShow(next); setNumber(1); }} />
          <label className="flex items-center gap-2 text-sm text-ink-muted">
            <input type="checkbox" checked={mine} onChange={(e) => { setMine(e.target.checked); setNumber(1); }} />
            Only mine
          </label>
        </div>
        {page === null ? (
          <Notice tone={loadError ? "error" : "info"}>{loadError || "Loading"}</Notice>
        ) : page.items.length === 0 ? (
          <EmptyState icon={ListTodo} title={NONE[show]} />
        ) : (
          <>
            <Wide>
              <table className="w-full text-base" aria-label="To-dos">
                <thead>
                  <tr>
                    <th className="text-left">To-do</th>
                    <th className="text-left">Property</th>
                    <th className="text-left">Assigned to</th>
                    <th className="text-left">Due</th>
                    <th className="text-left">Status</th>
                    <th><span className="sr-only">Actions</span></th>
                  </tr>
                </thead>
                <tbody>
                  {page.items.map((todo) => {
                    const standing = todoStanding(todo);
                    return (
                      <tr key={todo.id} aria-label={todo.title}>
                        <td>
                          <span className="block font-medium">{todo.title}</span>
                          {todo.notes && <span className="block text-sm text-ink-muted">{todo.notes}</span>}
                          <span className="block text-xs text-ink-subtle">
                            {todo.done_at
                              ? `Done by ${todo.done_by} on ${usDate(todo.done_at.slice(0, 10))}`
                              : `Written by ${todo.written_by} on ${usDate(todo.written_at.slice(0, 10))}`}
                          </span>
                        </td>
                        <td className="text-ink-muted">{todo.property ?? ""}</td>
                        <td>{todo.assigned_to}</td>
                        <td className="whitespace-nowrap">{usDate(todo.due_on)}</td>
                        <td><Badge tone={standing.tone}>{standing.word}</Badge></td>
                        <td className="text-right">
                          {!todo.done_at && (
                            <span className="inline-flex gap-2">
                              <Button size="sm" variant="secondary" onClick={() => setPanel(todo)}>Reassign</Button>
                              <Button size="sm" variant="secondary" onClick={() => record(
                                `/api/todos/${todo.id}/done`, {}, () => `Done: ${todo.title}.`,
                              )}>Done</Button>
                            </span>
                          )}
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </Wide>
            <Pager page={page} onPage={setNumber} />
          </>
        )}
      </div>
      {panel === "add" && (
        <SidePanel title="Add to-do" onClose={() => setPanel("")}>
          <TodoForm me={me} record={recordThenClose} />
          <Notice tone="error">{error}</Notice>
        </SidePanel>
      )}
      {typeof panel === "object" && (
        <SidePanel title={`Reassign: ${panel.title}`} onClose={() => setPanel("")}>
          <AssignForm todo={panel} record={recordThenClose} />
          <Notice tone="error">{error}</Notice>
        </SidePanel>
      )}
    </Shell>
  );
}

function useStaff(): Member[] {
  const [staff, setStaff] = useState<Member[]>([]);
  useEffect(() => {
    api.get<Member[]>("/api/staff").then(setStaff).catch(() => setStaff([]));
  }, []);
  return staff;
}

function TodoForm({ me, record }: { me: Me; record: Recorder }) {
  const staff = useStaff();
  const [places, setPlaces] = useState<Groups | null>(null);
  const [title, setTitle] = useState("");
  const [notes, setNotes] = useState("");
  const [to, setTo] = useState(me.person_id);
  const [due, setDue] = useState(day(new Date()));
  const [property, setProperty] = useState("");
  useEffect(() => {
    api.get<Groups>("/api/property-groups").then(setPlaces).catch(() => setPlaces(null));
  }, []);
  return (
    <Form className="flex flex-col gap-3" onSubmit={(event) => {
      event.preventDefault();
      const body = {
        title, notes: notes.trim() || null, assigned_to: to, due_on: due, property_id: property || null,
      };
      return record<Todo>("/api/todos", body, (made) => `Added: ${made.title}, for ${made.assigned_to}.`);
    }}>
      <Field label="To-do" id="todo-title" required maxLength={200} value={title} onChange={(e) => setTitle(e.target.value)} />
      <label htmlFor="todo-notes" className="flex flex-col gap-1.5 text-sm text-ink-muted">
        Notes (optional)
        <textarea id="todo-notes" rows={3} maxLength={4000} value={notes} onChange={(e) => setNotes(e.target.value)}
          className="rounded-sm border border-line-strong bg-sunken px-3 py-2 text-base text-ink" />
      </label>
      <Select label="Assign to" id="todo-to" value={to} onChange={(e) => setTo(e.target.value)}>
        {staff.length === 0 && <option value={me.person_id}>{me.name}</option>}
        {staff.map((s) => <option key={s.staff_id} value={s.staff_id}>{s.name}</option>)}
      </Select>
      <Field label="Due on" id="todo-due" type="date" required value={due} onChange={(e) => setDue(e.target.value)} />
      <Select label="About a property (optional)" id="todo-property" value={property} onChange={(e) => setProperty(e.target.value)}>
        <option value="">None</option>
        {places?.properties.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
      </Select>
      <div><Button type="submit">Add to-do</Button></div>
    </Form>
  );
}

function AssignForm({ todo, record }: { todo: Todo; record: Recorder }) {
  const staff = useStaff();
  const [to, setTo] = useState(todo.assigned_to_id);
  const [due, setDue] = useState(todo.due_on);
  return (
    <Form className="flex flex-col gap-3" onSubmit={(event) => {
      event.preventDefault();
      return record<Todo>(`/api/todos/${todo.id}/assign`, { assigned_to: to, due_on: due },
        (moved) => `${moved.title}: ${moved.assigned_to}'s, due ${usDate(moved.due_on)}.`);
    }}>
      <Select label="Assign to" id="assign-to" value={to} onChange={(e) => setTo(e.target.value)}>
        {staff.length === 0 && <option value={todo.assigned_to_id}>{todo.assigned_to}</option>}
        {staff.map((s) => <option key={s.staff_id} value={s.staff_id}>{s.name}</option>)}
      </Select>
      <Field label="Due on" id="assign-due" type="date" required value={due} onChange={(e) => setDue(e.target.value)} />
      <div><Button type="submit">Save</Button></div>
    </Form>
  );
}
