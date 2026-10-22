import { money } from "./money";

// A bank's or lockbox's payment file (src/pms/api/payment_files.py, the founder's review q035).
export type LineStatus = "to record" | "recorded" | "waiting" | "set aside";

export type FileLine = {
  id: string | null;
  line_no: number;
  received_on: string;
  amount: string;
  property: string | null;
  unit: string | null;
  payer: string | null;
  reference: string | null;
  method: string | null;
  status: LineStatus;
  lease_id: string | null;
  lease: string | null;
  note: string | null;
  by: string | null;
};

export type Preview = {
  lines: FileLine[];
  to_record: number;
  to_record_total: string;
  waiting: number;
  total: string;
  came_in: string | null;
};

export type FileSummary = {
  id: string;
  file_name: string;
  came_in: string;
  brought_by: string | null;
  lines: number;
  total: string;
  recorded: number;
  recorded_total: string;
  waiting: number;
  set_aside: number;
};

export type FileDetail = FileSummary & { items: FileLine[] };

// How a line names its lease, as the file said it: "Birch Flats 2B", "Morgan Demo".
export function named(line: FileLine): string {
  return [line.property, line.unit].filter(Boolean).join(" ") || line.payer || "";
}

function payments(n: number): string {
  return n === 1 ? "1 payment" : `${n} payments`;
}

export function previewLine(preview: Preview): string {
  const waiting = preview.waiting === 0 ? "" : `; ${preview.waiting} will wait for you`;
  return `${payments(preview.to_record)} (${money(preview.to_record_total)}) will be recorded${waiting}.`;
}

export function cameInLine(file: FileSummary): string {
  const waiting = file.waiting === 0 ? "nothing waits" : `${file.waiting} wait for you`;
  return `${file.file_name}: ${payments(file.recorded)} recorded (${money(file.recorded_total)}); ${waiting}.`;
}
