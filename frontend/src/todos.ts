import { usDate } from "./money";

// Staff to-dos as the API sends them (src/pms/api/todos.py, migration 20261023000203).
export type Todo = {
  id: string;
  title: string;
  notes: string | null;
  property_id: string | null;
  property: string | null;
  assigned_to_id: string;
  assigned_to: string;
  due_on: string;
  // Open and due before today.
  overdue: boolean;
  written_by: string;
  written_at: string;
  done_by: string | null;
  done_at: string | null;
};

// A to-do on the Dashboard: "Call the roofer (Cedar House): due 10/25/2026", or overdue.
export function todoLine(todo: Todo, today: string): string {
  const what = todo.property ? `${todo.title} (${todo.property})` : todo.title;
  if (todo.overdue) return `${what}: overdue, due ${usDate(todo.due_on)}`;
  return `${what}: due ${todo.due_on === today ? "today" : usDate(todo.due_on)}`;
}

// Where a to-do stands, as a badge.
export function todoStanding(todo: Todo): { word: string; tone: "positive" | "danger" | "neutral" } {
  if (todo.done_at) return { word: "done", tone: "positive" };
  if (todo.overdue) return { word: "overdue", tone: "danger" };
  return { word: "open", tone: "neutral" };
}
