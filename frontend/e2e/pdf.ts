import { inflateSync } from "node:zlib";

// The text a PDF of the PMS's own shows, as a viewer copies it: each text block's strings in
// order, a block a line. The PDFs embed IBM Plex Sans (src/pms/pdf_fonts.py), so a string holds
// two-byte glyph codes, and its font's ToUnicode map turns them back into letters. It reads only
// what fpdf2 writes: objects with a direct /Length, deflated streams, bfchar and bfrange maps.
export function pdfText(data: Buffer): string {
  const objects = readObjects(data.toString("latin1"));
  const maps = new Map<number, Map<number, string>>();
  for (const [id, object] of objects) {
    if (object.stream.includes("begincmap")) maps.set(id, toUnicode(object.stream));
  }
  const fonts = new Map<string, Map<number, string>>();
  for (const object of objects.values()) {
    for (const [, block] of object.dict.matchAll(/\/Font\s*<<([^>]*)>>/g)) {
      for (const [, name, ref] of block!.matchAll(/\/([\w.]+)\s+(\d+) 0 R/g)) {
        const map = maps.get(Number(/\/ToUnicode (\d+) 0 R/.exec(objects.get(Number(ref))?.dict ?? "")?.[1]));
        if (map) fonts.set(name!, map);
      }
    }
  }
  const lines: string[] = [];
  for (const object of objects.values()) {
    if (/\bBT\b/.test(object.stream) && /\bTf\b/.test(object.stream)) lines.push(...shown(object.stream, fonts));
  }
  return lines.join("\n");
}

type PdfObject = { dict: string; stream: string };

function readObjects(raw: string): Map<number, PdfObject> {
  const objects = new Map<number, PdfObject>();
  const header = /(\d+) 0 obj\b/g;
  for (let match = header.exec(raw); match; match = header.exec(raw)) {
    const from = match.index + match[0].length;
    const ends = raw.indexOf("endobj", from);
    const opens = raw.indexOf("stream\n", from);
    if (opens < 0 || opens > ends) {
      objects.set(Number(match[1]), { dict: raw.slice(from, ends), stream: "" });
      continue;
    }
    const dict = raw.slice(from, opens);
    const length = Number(/\/Length (\d+)(?! \d+ R)/.exec(dict)?.[1] ?? 0);
    const bytes = Buffer.from(raw.slice(opens + 7, opens + 7 + length), "latin1");
    let stream: string;
    try {
      stream = dict.includes("/FlateDecode") ? inflateSync(bytes).toString("latin1") : bytes.toString("latin1");
    } catch {
      stream = "";
    }
    objects.set(Number(match[1]), { dict, stream });
    header.lastIndex = raw.indexOf("endobj", opens + 7 + length);
  }
  return objects;
}

function toUnicode(cmap: string): Map<number, string> {
  const map = new Map<number, string>();
  for (const [, section] of cmap.matchAll(/beginbfchar([\s\S]*?)endbfchar/g)) {
    for (const [, code, text] of section!.matchAll(/<([0-9A-Fa-f]+)>\s*<([0-9A-Fa-f]+)>/g)) {
      map.set(parseInt(code!, 16), utf16(text!));
    }
  }
  for (const [, section] of cmap.matchAll(/beginbfrange([\s\S]*?)endbfrange/g)) {
    for (const [, low, high, first] of section!.matchAll(/<([0-9A-Fa-f]+)>\s*<([0-9A-Fa-f]+)>\s*<([0-9A-Fa-f]+)>/g)) {
      for (let code = parseInt(low!, 16), at = parseInt(first!, 16); code <= parseInt(high!, 16); code++, at++) {
        map.set(code, String.fromCodePoint(at));
      }
    }
  }
  return map;
}

function utf16(hex: string): string {
  const units: number[] = [];
  for (let at = 0; at < hex.length; at += 4) units.push(parseInt(hex.slice(at, at + 4), 16));
  return String.fromCharCode(...units);
}

// Each BT ... ET block's strings, decoded with the font the last Tf named. The font carries from
// block to block, as a PDF's text state does: fpdf2 sets it in a block of its own.
function shown(content: string, fonts: Map<string, Map<number, string>>): string[] {
  const lines: string[] = [];
  let font: Map<number, string> | undefined;
  for (const [, block] of content.matchAll(/\bBT\b([\s\S]*?)\bET\b/g)) {
    let line = "";
    const token = /\/([\w.]+)\s+[\d.]+\s+Tf|\((?:\\[\s\S]|[^\\)])*\)|<([0-9A-Fa-f\s]*)>/g;
    for (const [whole, name, hex] of block!.matchAll(token)) {
      if (name !== undefined) font = fonts.get(name);
      else line += decode(hex !== undefined ? fromHex(hex) : unescape(whole.slice(1, -1)), font);
    }
    if (line) lines.push(line);
  }
  return lines;
}

function decode(bytes: string, font: Map<number, string> | undefined): string {
  if (!font) return bytes;
  let text = "";
  for (let at = 0; at + 1 < bytes.length; at += 2) {
    text += font.get(bytes.charCodeAt(at) * 256 + bytes.charCodeAt(at + 1)) ?? "�";
  }
  return text;
}

function fromHex(hex: string): string {
  const digits = hex.replace(/\s/g, "");
  let bytes = "";
  for (let at = 0; at < digits.length; at += 2) bytes += String.fromCharCode(parseInt(digits.slice(at, at + 2).padEnd(2, "0"), 16));
  return bytes;
}

// A literal string's escapes (PDF 1.7, 7.3.4.2).
function unescape(literal: string): string {
  const named: Record<string, string> = { n: "\n", r: "\r", t: "\t", b: "\b", f: "\f" };
  return literal.replace(/\\([0-7]{1,3}|[\s\S])/g, (_, escaped: string) =>
    /^[0-7]/.test(escaped) ? String.fromCharCode(parseInt(escaped, 8)) : (named[escaped] ?? (escaped === "\n" ? "" : escaped)),
  );
}
