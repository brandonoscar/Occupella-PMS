// Documents as the API sends them (src/pms/api/documents.py), and how they read.

export type Kind = "w9" | "receipt" | "move_out_photo" | "owner_document" | "work_order_photo";
export type ContentType = "application/pdf" | "image/jpeg" | "image/png";
export type Subject = { id: string; label: string };
export type Document = {
  id: string;
  kind: Kind;
  about_id: string;
  about: string;
  title: string;
  content_type: ContentType;
  size: number;
  uploaded_by: string | null;
  uploaded_at: string;
};
export type Library = { documents: Document[]; subjects: Record<Kind, Subject[]> };
export type OwnerDocument = { id: string; title: string; content_type: ContentType; size: number; uploaded_at: string };

// Each kind, as the page names it and what it is about.
export const KINDS: { kind: Kind; label: string; about: string }[] = [
  { kind: "w9", label: "W-9", about: "Vendor" },
  { kind: "receipt", label: "Receipt", about: "Bill" },
  { kind: "move_out_photo", label: "Move-out photo", about: "Lease" },
  { kind: "owner_document", label: "Owner document", about: "Owner" },
  { kind: "work_order_photo", label: "Work order photo", about: "Work order" },
];

export const MAX_BYTES = 5 * 1024 * 1024;
const TYPES: Record<string, ContentType> = {
  "application/pdf": "application/pdf",
  "image/jpeg": "image/jpeg",
  "image/png": "image/png",
};

// The type a chosen file is sent as, or null for one a document can't be.
export function typeOf(file: { type: string }): ContentType | null {
  return TYPES[file.type] ?? null;
}

export function kindLabel(kind: Kind): string {
  return KINDS.find((k) => k.kind === kind)?.label ?? kind;
}

export function sizeLabel(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${Math.round(bytes / 1024)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

// A file's name without its extension: the title a document starts with.
export function titleOf(name: string): string {
  return name.replace(/\.[A-Za-z0-9]{1,5}$/, "");
}

// Bytes as base64, a chunk at a time so a large file doesn't overflow the call stack.
export function toBase64(bytes: Uint8Array): string {
  let text = "";
  for (let i = 0; i < bytes.length; i += 0x8000) {
    text += String.fromCharCode(...bytes.subarray(i, i + 0x8000));
  }
  return btoa(text);
}
