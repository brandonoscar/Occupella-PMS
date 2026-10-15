// A lease's move-in and move-out inspections as the API sends them (src/pms/api/inspections.py).
export type Condition = "good" | "fair" | "poor" | "damaged" | "missing";
export const CONDITIONS: Condition[] = ["good", "fair", "poor", "damaged", "missing"];
export type InspectionItem = { room: string; condition: Condition; note: string | null };
export type Inspection = {
  id: string;
  kind: "move-in" | "move-out";
  inspected_on: string;
  notes: string | null;
  recorded_by: string | null;
  recorded_at: string;
  items: InspectionItem[];
};
export type NewRoom = { room: string; condition: Condition; note: string };

// The rooms most inspections start with; staff add or remove any.
export const ROOMS = ["Kitchen", "Living room", "Bedroom", "Bathroom"];

// What the API takes: rooms named, blank notes left out.
export function itemsToSend(rooms: NewRoom[]): { room: string; condition: Condition; note?: string }[] {
  return rooms
    .filter((r) => r.room.trim())
    .map((r) => ({ room: r.room.trim(), condition: r.condition, ...(r.note.trim() ? { note: r.note.trim() } : {}) }));
}

// "Move-in, 2026-01-01: 4 rooms, 1 damaged".
export function inspectionSummary(inspection: Inspection): string {
  const kind = inspection.kind === "move-in" ? "Move-in" : "Move-out";
  const rooms = inspection.items.length;
  const bad = inspection.items.filter((i) => i.condition === "damaged" || i.condition === "missing").length;
  return `${kind}, ${inspection.inspected_on}: ${rooms} room${rooms === 1 ? "" : "s"}` +
    (bad ? `, ${bad} damaged or missing` : "");
}

export function inspectionReportHref(leaseId: string): string {
  return `/api/reports/trust_report_inspection?${new URLSearchParams({ arg: leaseId, format: "pdf" })}`;
}
