// ADR 0010, rule 1: every action button has one plain line under it, or in its confirm dialog,
// saying what happens and what doesn't. A form's submit button is the action of every form, so
// each one carries `says` (components/Button.tsx); a submit that only shows something (a report's
// Show, a preview) changes nothing and needs none. This reads every <Button> in the portal's
// source, its attributes scanned to the tag's own `>` (braces and quotes skipped).
import { readdirSync, readFileSync, statSync } from "node:fs";
import { join, relative } from "node:path";
import { describe, expect, it } from "vitest";

const SRC = join(process.cwd(), "src");

function sources(dir: string): string[] {
  return readdirSync(dir).flatMap((name) => {
    const path = join(dir, name);
    if (statSync(path).isDirectory()) return sources(path);
    return name.endsWith(".tsx") && !name.endsWith(".test.tsx") ? [path] : [];
  });
}

type Tag = { file: string; line: number; attributes: string; label: string };

// Each <Button ...>label</Button> in a file: its attributes as written and its label's text.
function buttons(file: string, text: string): Tag[] {
  const found: Tag[] = [];
  for (const match of text.matchAll(/<Button\b/g)) {
    let at = match.index + "<Button".length;
    let depth = 0;
    let quote: string | null = null;
    for (; at < text.length; at++) {
      const c = text[at];
      if (quote) {
        if (c === quote && text[at - 1] !== "\\") quote = null;
      } else if (c === '"' || c === "'" || c === "`") quote = c;
      else if (c === "{") depth++;
      else if (c === "}") depth--;
      else if (c === ">" && depth === 0) break;
    }
    const attributes = text.slice(match.index + "<Button".length, at);
    const selfClosing = attributes.trimEnd().endsWith("/");
    const end = selfClosing ? at : text.indexOf("</Button>", at);
    const label = selfClosing ? "" : text.slice(at + 1, end).replace(/\s+/g, " ").trim();
    const line = text.slice(0, match.index).split("\n").length;
    found.push({ file, line, attributes, label });
  }
  return found;
}

// Submit buttons that show something and change nothing.
const SHOWS_ONLY = new Set(["Show", "Preview"]);

const ALL = sources(SRC).flatMap((path) => buttons(relative(SRC, path), readFileSync(path, "utf8")));

describe("rule 1: a plain line under every action button", () => {
  it("finds the portal's buttons", () => {
    expect(ALL.filter((tag) => /type="submit"/.test(tag.attributes)).length).toBeGreaterThan(100);
  });

  it("every submit button says what it does and what it doesn't", () => {
    const missing = ALL.filter(
      (tag) => /type="submit"/.test(tag.attributes) && !/\bsays=/.test(tag.attributes) && !SHOWS_ONLY.has(tag.label),
    ).map((tag) => `${tag.file}:${tag.line} ${tag.label}`);
    expect(missing).toEqual([]);
  });

  it("reads a tag's attributes past arrows and strings", () => {
    const [tag] = buttons("x.tsx", `<Button type="submit" onClick={() => go(">")} says={\`a > b\`}>Pay {money(x)}</Button>`);
    expect(tag?.attributes).toContain("says=");
    expect(tag?.label).toBe("Pay {money(x)}");
  });
});
